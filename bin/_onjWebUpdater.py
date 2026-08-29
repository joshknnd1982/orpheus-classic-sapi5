# Self-hosted update checker for Orpheus Classic.

import json
import hashlib
import os
import shutil
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile

import addonHandler
import config
import core
import globalVars
import gui
import wx
from core import callLater
from logHandler import log

try:
	from systemUtils import ExecAndPump
except ImportError:
	from gui import ExecAndPump


CHECK_INTERVAL_MS = 86400 * 1000
RETRY_INTERVAL_MS = 600 * 1000
DOWNLOAD_BLOCK_SIZE = 8192
ORPHEUS_HOST_EXE = "orpheus-classic-host.exe"
HELPER_SCRIPT_PREFIX = "orpheusClassicApplyUpdate"
SELF_STAGED_DIR_NAME = "orpheusClassic.stagedUpdate"
UPDATER_WORK_DIR_NAME = "webUpdater"


def _parseVersion(version):
	parts = []
	for part in str(version).strip().lstrip("vV").replace("-dev", "").split("."):
		try:
			parts.append(int(part))
		except ValueError:
			break
	return tuple(parts) if parts else (0,)


def _isNewerVersion(newVersion, currentVersion):
	return _parseVersion(newVersion) > _parseVersion(currentVersion)


def _absoluteUrl(baseUrl, value):
	return urllib.parse.urljoin(baseUrl, str(value or ""))


def _downloadFile(url, dest, update=None):
	req = urllib.request.Request(url, headers={"User-Agent": "Orpheus Classic NVDA addon updater"})
	with urllib.request.urlopen(req, timeout=120) as remote:
		size = int(remote.headers.get("content-length") or 0)
		read = 0
		with open(dest, "wb") as local:
			while True:
				block = remote.read(DOWNLOAD_BLOCK_SIZE)
				if not block:
					break
				local.write(block)
				read += len(block)
				if update and size and update(int(read / size * 100)):
					return False
	return True


def _verifyDownload(path, info):
	expectedSize = info.get("size")
	if expectedSize:
		actualSize = os.path.getsize(path)
		if int(expectedSize) != actualSize:
			raise RuntimeError("Downloaded size mismatch. Expected %s, got %s." % (expectedSize, actualSize))
	expectedSha256 = str(info.get("sha256", "") or "").strip().lower()
	if not expectedSha256:
		raise RuntimeError("The update manifest did not include a SHA-256 hash.")
	hashObj = hashlib.sha256()
	with open(path, "rb") as f:
		while True:
			block = f.read(DOWNLOAD_BLOCK_SIZE)
			if not block:
				break
			hashObj.update(block)
	actualSha256 = hashObj.hexdigest().lower()
	if actualSha256 != expectedSha256:
		raise RuntimeError("Downloaded hash mismatch. Expected %s, got %s." % (expectedSha256, actualSha256))


def _safeExtractZip(zipPath, destination):
	destination = os.path.abspath(destination)
	with zipfile.ZipFile(zipPath, "r") as archive:
		for member in archive.infolist():
			name = member.filename.replace("\\", "/")
			if not name or name.startswith("/") or ".." in name.split("/"):
				raise RuntimeError("Unsafe path in add-on archive: %s" % member.filename)
			target = os.path.abspath(os.path.join(destination, *name.split("/")))
			if not target.startswith(destination + os.sep) and target != destination:
				raise RuntimeError("Unsafe path in add-on archive: %s" % member.filename)
			if member.is_dir() or name.endswith("/"):
				os.makedirs(target, exist_ok=True)
				continue
			os.makedirs(os.path.dirname(target), exist_ok=True)
			with archive.open(member, "r") as source, open(target, "wb") as dest:
				while True:
					block = source.read(DOWNLOAD_BLOCK_SIZE)
					if not block:
						break
					dest.write(block)


def _stageSelfUpdatePackage(addonPath, addonName):
	bundle = addonHandler.AddonBundle(addonPath)
	if str(bundle.manifest.get("name") or "").casefold() != str(addonName or "").casefold():
		raise RuntimeError("Downloaded add-on name does not match %s" % addonName)
	updatesDir = _updaterWorkDir(addonName)
	stagedDir = os.path.join(updatesDir, SELF_STAGED_DIR_NAME)
	tempDir = "%s-%d" % (stagedDir, os.getpid())
	for path in (tempDir, stagedDir):
		if os.path.exists(path):
			shutil.rmtree(path, ignore_errors=True)
	os.makedirs(tempDir, exist_ok=True)
	try:
		_safeExtractZip(addonPath, tempDir)
		if not os.path.isfile(os.path.join(tempDir, "manifest.ini")):
			raise RuntimeError("Downloaded add-on archive has no manifest.ini")
		os.rename(tempDir, stagedDir)
	except Exception:
		shutil.rmtree(tempDir, ignore_errors=True)
		raise
	return stagedDir


def _updaterWorkDir(addonName):
	return os.path.join(globalVars.appArgs.configPath, addonName, UPDATER_WORK_DIR_NAME)


def cleanupStaleHelpers(addonName):
	if os.name != "nt":
		return
	try:
		updatesDir = _updaterWorkDir(addonName)
		if not os.path.isdir(updatesDir):
			return
		for name in os.listdir(updatesDir):
			if (
				(name.startswith(HELPER_SCRIPT_PREFIX) and name.endswith((".ps1", ".running")))
				or name in ("orpheusClassicUpdateTrace.log",)
			):
				try:
					os.remove(os.path.join(updatesDir, name))
				except Exception:
					pass
	except Exception:
		log.debugWarning("Could not clean stale Orpheus Classic updater helpers", exc_info=True)


def _downloadWithProgress(url, dest, title, message):
	gui.mainFrame.prePopup()
	progressDialog = wx.ProgressDialog(
		title,
		message,
		style=wx.PD_CAN_ABORT | wx.PD_ELAPSED_TIME | wx.PD_REMAINING_TIME | wx.PD_AUTO_HIDE,
		parent=gui.mainFrame,
	)
	progressDialog.CentreOnScreen()
	progressDialog.Raise()

	def update(value):
		return not progressDialog.Update(value)[0]

	try:
		result = ExecAndPump(_downloadFile, url, dest, update)
		if getattr(result, "funcRes", True) is False:
			return False
		return True
	except Exception:
		log.error("Error downloading add-on update from %s", url, exc_info=True)
		gui.messageBox(
			_("Unable to download the update. Check your internet connection and try again later."),
			_("Error downloading update"),
			wx.OK | wx.ICON_ERROR,
			gui.mainFrame,
		)
		return False
	finally:
		progressDialog.Destroy()
		gui.mainFrame.postPopup()


def _installAddon(addonPath):
	from gui.message import DisplayableError

	try:
		bundle = addonHandler.AddonBundle(addonPath)
		addonName = bundle.manifest.get("name")
		_prepareForInstall(addonName)
		prevAddon = None
		for addon in addonHandler.getAvailableAddons():
			if addon.name == addonName:
				prevAddon = addon
				break
		result = ExecAndPump(addonHandler.installAddonBundle, bundle)
		addonObj = result.funcRes
		if getattr(bundle, "_installExceptions", None):
			for exc in bundle._installExceptions:
				log.error(exc, exc_info=True)
			raise DisplayableError(_("Failed to install add-on from %s") % addonPath)
		if prevAddon and str(addonName or "").casefold() != "orpheusclassic":
			prevAddon.requestRemove()
		if addonObj:
			addonObj._cleanupAddonImports()
		return True
	except Exception:
		log.error("Error installing add-on update from %s", addonPath, exc_info=True)
		gui.messageBox(
			_("Failed to install the update."),
			_("Update failed"),
			wx.OK | wx.ICON_ERROR,
			gui.mainFrame,
		)
		return False


def _isOrpheusHostRunning():
	if os.name != "nt":
		return False
	try:
		startupinfo = subprocess.STARTUPINFO()
		startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
		result = subprocess.run(
			["tasklist", "/FI", "IMAGENAME eq %s" % ORPHEUS_HOST_EXE],
			stdout=subprocess.PIPE,
			stderr=subprocess.DEVNULL,
			text=True,
			startupinfo=startupinfo,
			check=False,
		)
	except Exception:
		log.debugWarning("Could not query Orpheus Classic host process state", exc_info=True)
		return False
	return ORPHEUS_HOST_EXE.lower() in (result.stdout or "").lower()


def _waitForOrpheusHostExit(timeout=5.0):
	deadline = time.time() + timeout
	while time.time() < deadline:
		if not _isOrpheusHostRunning():
			return True
		time.sleep(0.2)
	return not _isOrpheusHostRunning()


def _waitForOrpheusHostStopped(timeout=10.0):
	if _waitForOrpheusHostExit(timeout):
		return True
	try:
		startupinfo = None
		if os.name == "nt":
			startupinfo = subprocess.STARTUPINFO()
			startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
		subprocess.run(
			["taskkill", "/IM", ORPHEUS_HOST_EXE, "/T", "/F"],
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL,
			startupinfo=startupinfo,
			check=False,
		)
	except Exception:
		log.debugWarning("Could not force-stop Orpheus Classic host", exc_info=True)
	return _waitForOrpheusHostExit(5.0)


def _prepareForInstall(addonName):
	if str(addonName or "").casefold() != "orpheusclassic":
		return
	try:
		import synthDriverHandler
		current = synthDriverHandler.getSynth()
		if getattr(current, "name", "") == "orpheusClassic":
			try:
				current.terminate()
			except Exception:
				log.debugWarning("Could not explicitly terminate Orpheus Classic synth before update install", exc_info=True)
			time.sleep(1.0)
	except Exception:
		log.debugWarning("Could not terminate Orpheus Classic before update install", exc_info=True)
	if not _waitForOrpheusHostStopped():
		log.warning("Orpheus Classic host was still running after update pre-install cleanup")


def _writeExternalApplyHelper(addonName, stagedDir):
	if os.name != "nt":
		return None
	updatesDir = _updaterWorkDir(addonName)
	os.makedirs(updatesDir, exist_ok=True)
	scriptPath = os.path.join(updatesDir, "%s-%d.ps1" % (HELPER_SCRIPT_PREFIX, int(time.time())))
	addonsDir = os.path.join(globalVars.appArgs.configPath, "addons")
	finalDir = os.path.join(addonsDir, addonName)
	pendingDir = os.path.join(addonsDir, "%s.pendingInstall" % addonName)
	stateFile = os.path.join(globalVars.appArgs.configPath, "addonsState.json")
	traceFile = os.path.join(updatesDir, "orpheusClassicUpdateTrace.log")
	nvdaExe = sys.executable
	script = r'''
$ErrorActionPreference = "SilentlyContinue"
$staged = __STAGED__
$pending = __PENDING__
$final = __FINAL__
$stateFile = __STATE__
$traceFile = __TRACE__
$nvdaExe = __NVDA__
$hostExe = __HOST__
$addonLower = __ADDONLOWER__
$scriptPath = $MyInvocation.MyCommand.Path
$runningMarker = "$scriptPath.running"

function Trace($message) {
	try {
		$stamp = (Get-Date).ToString("o")
		Add-Content -LiteralPath $traceFile -Value "$stamp $message"
	} catch {
	}
}

if (Test-Path -LiteralPath $runningMarker) {
	Trace "exit: running marker already exists for $scriptPath"
	exit
}
New-Item -ItemType File -Path $runningMarker -Force | Out-Null
Trace "start: helper=$scriptPath staged=$staged final=$final"

function Wait-NvdaExit {
	for ($i = 0; $i -lt 120; $i++) {
		if (-not (Get-Process -Name nvda -ErrorAction SilentlyContinue)) {
			return
		}
		Start-Sleep -Milliseconds 250
	}
}

function Stop-OrpheusHost {
	for ($i = 0; $i -lt 40; $i++) {
		$proc = Get-Process -Name ([System.IO.Path]::GetFileNameWithoutExtension($hostExe)) -ErrorAction SilentlyContinue
		if (-not $proc) {
			return
		}
		Start-Sleep -Milliseconds 250
	}
	taskkill /IM $hostExe /T /F | Out-Null
	Start-Sleep -Milliseconds 500
}

function Remove-PathWithRetry($path) {
	if (-not (Test-Path -LiteralPath $path)) {
		return $true
	}
	for ($i = 0; $i -lt 80; $i++) {
		try {
			Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction Stop
			return $true
		} catch {
			Start-Sleep -Milliseconds 250
		}
	}
	return -not (Test-Path -LiteralPath $path)
}

function Move-PathWithRetry($source, $dest) {
	for ($i = 0; $i -lt 80; $i++) {
		try {
			Move-Item -LiteralPath $source -Destination $dest -ErrorAction Stop
			return $true
		} catch {
			Start-Sleep -Milliseconds 250
		}
	}
	return $false
}

function Clear-AddonPendingState {
	if (-not (Test-Path -LiteralPath $stateFile)) {
		return
	}
	try {
		$json = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
		foreach ($field in @("pendingInstallsSet", "pendingRemovesSet", "pendingEnableSet", "pendingDisableSet")) {
			if ($null -ne $json.$field) {
				$json.$field = @($json.$field | Where-Object { ([string]$_).ToLowerInvariant() -ne $addonLower })
			}
		}
		$text = $json | ConvertTo-Json -Depth 10 -Compress
		[System.IO.File]::WriteAllText($stateFile, $text, [System.Text.UTF8Encoding]::new($false))
	} catch {
	}
}

Wait-NvdaExit
Trace "nvda exited"
Stop-OrpheusHost
if (Test-Path -LiteralPath $staged) {
	Trace "staged exists; replacing final add-on folder"
	Remove-PathWithRetry $final | Out-Null
	Move-PathWithRetry $staged $final | Out-Null
} else {
	Trace "staged folder missing; no replacement performed"
}
Remove-PathWithRetry $pending | Out-Null
Clear-AddonPendingState
$runningNvda = @(Get-Process -Name nvda -ErrorAction SilentlyContinue)
if ($runningNvda.Count -gt 0) {
	Trace "NVDA already running; not starting a second instance"
} else {
	Trace "starting NVDA: $nvdaExe"
	Start-Process -FilePath $nvdaExe
}
Remove-Item -LiteralPath $runningMarker -Force
Remove-Item -LiteralPath $scriptPath -Force
Trace "done"
'''.strip()
	replacements = {
		"__STAGED__": json.dumps(stagedDir),
		"__PENDING__": json.dumps(pendingDir),
		"__FINAL__": json.dumps(finalDir),
		"__STATE__": json.dumps(stateFile),
		"__TRACE__": json.dumps(traceFile),
		"__NVDA__": json.dumps(nvdaExe),
		"__HOST__": json.dumps(ORPHEUS_HOST_EXE),
		"__ADDONLOWER__": json.dumps(str(addonName).casefold()),
	}
	for key, value in replacements.items():
		script = script.replace(key, value)
	with open(scriptPath, "w", encoding="utf-8-sig") as f:
		f.write(script + "\n")
	return scriptPath


def _restartViaExternalApplyHelper(addonName, stagedDir):
	scriptPath = _writeExternalApplyHelper(addonName, stagedDir)
	if not scriptPath:
		return False
	try:
		startupinfo = subprocess.STARTUPINFO()
		startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
		subprocess.Popen(
			[
				"powershell.exe",
				"-NoProfile",
				"-ExecutionPolicy",
				"Bypass",
				"-File",
				scriptPath,
			],
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL,
			startupinfo=startupinfo,
		)
		core.quit()
		return True
	except Exception:
		log.debugWarning("Could not start Orpheus Classic update apply helper", exc_info=True)
		return False


class WebManifestUpdater:
	def __init__(self, addonName, addonLabel, manifestUrl):
		self.addonName = addonName
		self.addonLabel = addonLabel
		self.manifestUrl = manifestUrl
		self.updatesDir = _updaterWorkDir(addonName)
		self.stateFile = os.path.join(globalVars.appArgs.configPath, "%sWebUpdate.json" % addonName)
		self.timer = None
		self.isError = False
		self.state = self._loadState()

	def _loadState(self):
		try:
			with open(self.stateFile, "r", encoding="utf-8") as f:
				return json.load(f)
		except Exception:
			return {"lastCheck": 0, "pendingFile": ""}

	def _saveState(self):
		try:
			with open(self.stateFile, "w", encoding="utf-8") as f:
				json.dump(self.state, f)
		except Exception:
			log.debugWarning("Could not save add-on updater state for %s", self.addonName, exc_info=True)

	def start(self):
		if getattr(config, "isAppX", False):
			return
		cleanupStaleHelpers(self.addonName)
		self._scheduleNext()

	def stop(self):
		try:
			if self.timer and self.timer.IsRunning():
				self.timer.Stop()
		except Exception:
			pass
		self.timer = None

	def checkNow(self, fromGui=True):
		self._checkUpdate(fromGui=fromGui)

	def _scheduleNext(self):
		self.stop()
		if self.isError:
			nextTime = RETRY_INTERVAL_MS
		else:
			nextTime = int(CHECK_INTERVAL_MS - (time.time() * 1000 - self.state.get("lastCheck", 0)))
		if nextTime <= 0:
			nextTime = 10000
		self.timer = callLater(nextTime, self._autoCheckUpdate)

	def _autoCheckUpdate(self):
		wx.CallAfter(self._checkUpdate, False)

	def _currentAddon(self):
		for addon in addonHandler.getAvailableAddons():
			if addon.name == self.addonName:
				return addon
		return None

	def _getUpdateInfo(self):
		req = urllib.request.Request(self.manifestUrl, headers={"User-Agent": "Orpheus Classic NVDA addon updater"})
		try:
			with urllib.request.urlopen(req, timeout=20) as response:
				data = json.loads(response.read().decode("utf-8"))
		except IOError as e:
			if getattr(e, "reason", None) and isinstance(e.reason, ssl.SSLCertVerificationError):
				raise
			raise

		version = str(data.get("version", "")).strip().lstrip("vV")
		if not version:
			raise RuntimeError("Update manifest has no version")
		fileName = str(data.get("fileName", "") or data.get("archiveName", "")).strip() or ("%s-%s.nvda-addon" % (self.addonName, version))
		downloadUrl = str(data.get("downloadUrl", "") or data.get("archiveUrl", "")).strip()
		if not downloadUrl:
			downloadUrl = _absoluteUrl(self.manifestUrl, fileName)
		if not downloadUrl.lower().endswith(".nvda-addon"):
			raise RuntimeError("Update manifest download URL is not an NVDA add-on")
		return {
			"version": version,
			"name": fileName,
			"downloadUrl": downloadUrl,
			"body": data.get("notes") or data.get("changelog") or _("No release notes available."),
			"size": data.get("size"),
			"sha256": data.get("sha256"),
		}

	def _checkUpdate(self, fromGui=False):
		if getattr(config, "isAppX", False):
			return
		pendingFile = self.state.get("pendingFile", "")
		if pendingFile and os.path.exists(pendingFile):
			return self._installDownloaded(pendingFile)

		current = self._currentAddon()
		if not current:
			log.debugWarning("Could not find current add-on %s for updater", self.addonName)
			return

		try:
			info = self._getUpdateInfo()
			self.isError = False
		except Exception:
			self.isError = True
			log.debugWarning("Could not check web update for %s", self.addonName, exc_info=True)
			if fromGui:
				gui.messageBox(
					_("Unable to check for updates right now."),
					_("Update check failed"),
					wx.OK | wx.ICON_ERROR,
					gui.mainFrame,
				)
			return self._scheduleNext()

		self.state["lastCheck"] = time.time() * 1000
		self._saveState()

		if not _isNewerVersion(info["version"], current.version):
			if fromGui:
				gui.messageBox(
					_("There are no updates available for %s.") % self.addonLabel,
					_("No updates available"),
					wx.OK | wx.ICON_INFORMATION,
					gui.mainFrame,
				)
			return self._scheduleNext()

		notes = info.get("body", "")
		if isinstance(notes, list):
			notes = "\n".join("- %s" % item for item in notes)
		message = _(
			"A new version of {addon} is available: {version}.\n\n"
			"If you choose Yes, the Orpheus Classic voice will stop while the update is installed, "
			"so speech may be silent for a short time. This is expected.\n\n"
			"When the update has completed, NVDA will restart automatically. "
			"No extra confirmation will be shown after speech has stopped.\n\n"
			"Do you want to download and install it now?\n\n"
			"{notes}"
		).format(addon=self.addonLabel, version=info["version"], notes=notes)
		res = gui.messageBox(
			message,
			_("Update available"),
			wx.YES | wx.NO | wx.ICON_INFORMATION,
			gui.mainFrame,
		)
		if res == wx.YES:
			self._downloadAndInstall(info)
		self._scheduleNext()

	def _downloadAndInstall(self, info):
		try:
			os.makedirs(self.updatesDir, exist_ok=True)
		except Exception:
			log.error("Unable to create add-on updates directory %s", self.updatesDir, exc_info=True)
			return
		dest = os.path.join(self.updatesDir, info["name"])
		if not _downloadWithProgress(
			info["downloadUrl"],
			dest,
			_("Downloading %s update") % self.addonLabel,
			_("Downloading update"),
		):
			return
		try:
			_verifyDownload(dest, info)
		except Exception:
			log.error("Downloaded update for %s failed verification", self.addonName, exc_info=True)
			try:
				os.remove(dest)
			except Exception:
				pass
			gui.messageBox(
				_("The downloaded update could not be verified and was not installed."),
				_("Update failed"),
				wx.OK | wx.ICON_ERROR,
				gui.mainFrame,
			)
			return
		self.state["pendingFile"] = dest
		self._saveState()
		self._installDownloaded(dest)

	def _installDownloaded(self, dest):
		if self.addonName.casefold() == "orpheusclassic":
			try:
				_prepareForInstall(self.addonName)
				stagedDir = _stageSelfUpdatePackage(dest, self.addonName)
			except Exception:
				log.error("Error staging Orpheus Classic self-update from %s", dest, exc_info=True)
				gui.messageBox(
					_("Failed to stage the update."),
					_("Update failed"),
					wx.OK | wx.ICON_ERROR,
					gui.mainFrame,
				)
				return
			self.state["pendingFile"] = ""
			self._saveState()
			try:
				os.remove(dest)
			except Exception:
				pass
			if not _restartViaExternalApplyHelper(self.addonName, stagedDir):
				core.restart()
			return
		gui.mainFrame.prePopup()
		try:
			result = _installAddon(dest)
		finally:
			gui.mainFrame.postPopup()
		if result:
			self.state["pendingFile"] = ""
			self._saveState()
			try:
				os.remove(dest)
			except Exception:
				pass
			core.restart()

