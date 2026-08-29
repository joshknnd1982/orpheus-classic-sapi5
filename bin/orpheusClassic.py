import globalPluginHandler
import addonHandler
import gui
import wx
import speech
import globalVars
import config
import ui
import queueHandler
import base64
import json
import os
import re
import shutil
import time
import zlib
import winreg
from logHandler import log

try:
	from ._onjWebUpdater import WebManifestUpdater, cleanupStaleHelpers
except Exception:
	WebManifestUpdater = None
	cleanupStaleHelpers = None

addonHandler.initTranslation()

CONF_SECTION = "orpheusClassic"
CONF_SNAPSHOT = "dolphinRegistrySnapshot"
CONF_UPDATED = "dolphinRegistrySnapshotUpdated"
CONF_AUTO_RESTORE = "restoreDolphinRegistryOnStartup"
DATA_DIR_NAME = "orpheusClassic"
DICTIONARY_FILE_NAME = "dictionary.json"
DEFAULT_DICTIONARY_FILE_NAME = "defaultDictionary.json"
UPDATE_MANIFEST_URL = "https://onj.me/nvda/.orpheusClassic.json"
UPDATE_MANIFEST_TEST_URL = "https://onj.me/nvda/.orpheusClassicTest.json"
UPDATE_TEST_FLAG = "useTestUpdateFeed"
CURRENT_UPDATER = None
EXCEPTION_MATCH_ANYWHERE = "anywhere"
EXCEPTION_MATCH_WHOLE_WORD = "wholeWord"
EXCEPTION_MATCH_REGEX = "regularExpression"
EXCEPTION_MATCH_STARTS_WITH = "startsWith"
EXCEPTION_MATCH_ENDS_WITH = "endsWith"
EXCEPTION_MATCH_TYPES = {
	EXCEPTION_MATCH_ANYWHERE,
	EXCEPTION_MATCH_WHOLE_WORD,
	EXCEPTION_MATCH_REGEX,
	EXCEPTION_MATCH_STARTS_WITH,
	EXCEPTION_MATCH_ENDS_WITH,
}
MATCH_TYPE_LABELS = {
	EXCEPTION_MATCH_WHOLE_WORD: _("Whole word"),
	EXCEPTION_MATCH_ANYWHERE: _("Anywhere"),
	EXCEPTION_MATCH_STARTS_WITH: _("Starts with"),
	EXCEPTION_MATCH_ENDS_WITH: _("Ends with"),
	EXCEPTION_MATCH_REGEX: _("Regular expression"),
}
MATCH_TYPE_VALUES = (
	EXCEPTION_MATCH_WHOLE_WORD,
	EXCEPTION_MATCH_ANYWHERE,
	EXCEPTION_MATCH_STARTS_WITH,
	EXCEPTION_MATCH_ENDS_WITH,
	EXCEPTION_MATCH_REGEX,
)
ORPHEUS_TEXT_FLAG_MAP = {
	"W": EXCEPTION_MATCH_WHOLE_WORD,
	"A": EXCEPTION_MATCH_ANYWHERE,
	"B": EXCEPTION_MATCH_STARTS_WITH,
	"E": EXCEPTION_MATCH_ENDS_WITH,
}
REGISTRY_ROOT = r"Software\Dolphin"
SNAPSHOT_VERSION = 1
DEFAULT_EXCEPTION_LANGUAGE = 44
ORPHEUS_REGISTRY_READ_ACCESS = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_32KEY", 0)
ORPHEUS_REGISTRY_WRITE_ACCESS = winreg.KEY_SET_VALUE | getattr(winreg, "KEY_WOW64_32KEY", 0)


def _ensure_config():
	if CONF_SECTION not in config.conf.spec:
		config.conf.spec[CONF_SECTION] = {}
	spec = config.conf.spec[CONF_SECTION]
	spec.setdefault(CONF_SNAPSHOT, "string(default='')")
	spec.setdefault(CONF_UPDATED, "string(default='')")
	spec.setdefault(CONF_AUTO_RESTORE, "boolean(default=True)")
	spec.setdefault(UPDATE_TEST_FLAG, "boolean(default=False)")
	_ = config.conf[CONF_SECTION]


def _value_to_snapshot(value, reg_type):
	if reg_type == winreg.REG_BINARY:
		return {"type": "REG_BINARY", "data": base64.b64encode(value).decode("ascii")}
	if reg_type == winreg.REG_DWORD:
		return {"type": "REG_DWORD", "data": int(value)}
	if reg_type == winreg.REG_QWORD:
		return {"type": "REG_QWORD", "data": int(value)}
	if reg_type in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
		return {"type": "REG_EXPAND_SZ" if reg_type == winreg.REG_EXPAND_SZ else "REG_SZ", "data": value}
	if reg_type == winreg.REG_MULTI_SZ:
		return {"type": "REG_MULTI_SZ", "data": list(value)}
	return {"type": int(reg_type), "data": value}


def _snapshot_to_value(item):
	reg_type = item["type"]
	value = item["data"]
	if reg_type == "REG_BINARY":
		return base64.b64decode(value.encode("ascii")), winreg.REG_BINARY
	if reg_type == "REG_DWORD":
		return int(value), winreg.REG_DWORD
	if reg_type == "REG_QWORD":
		return int(value), winreg.REG_QWORD
	if reg_type == "REG_EXPAND_SZ":
		return str(value), winreg.REG_EXPAND_SZ
	if reg_type == "REG_SZ":
		return str(value), winreg.REG_SZ
	if reg_type == "REG_MULTI_SZ":
		return list(value), winreg.REG_MULTI_SZ
	return value, int(reg_type)


def _read_key(relative_path):
	data = {"values": {}, "subkeys": {}}
	with winreg.OpenKey(winreg.HKEY_CURRENT_USER, relative_path, 0, ORPHEUS_REGISTRY_READ_ACCESS) as key:
		value_count = winreg.QueryInfoKey(key)[1]
		for index in range(value_count):
			name, value, reg_type = winreg.EnumValue(key, index)
			data["values"][name] = _value_to_snapshot(value, reg_type)
		subkey_count = winreg.QueryInfoKey(key)[0]
		for index in range(subkey_count):
			subkey_name = winreg.EnumKey(key, index)
			data["subkeys"][subkey_name] = _read_key(relative_path + "\\" + subkey_name)
	return data


def _write_key(relative_path, data):
	key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, relative_path, 0, ORPHEUS_REGISTRY_WRITE_ACCESS)
	try:
		for name, item in data.get("values", {}).items():
			value, reg_type = _snapshot_to_value(item)
			winreg.SetValueEx(key, name, 0, reg_type, value)
	finally:
		winreg.CloseKey(key)
	for subkey_name, subkey_data in data.get("subkeys", {}).items():
		_write_key(relative_path + "\\" + subkey_name, subkey_data)


def _create_registry_snapshot():
	root = _read_key(REGISTRY_ROOT)
	return {
		"version": SNAPSHOT_VERSION,
		"root": REGISTRY_ROOT,
		"created": time.strftime("%Y-%m-%d %H:%M:%S"),
		"data": root,
	}


def _encode_snapshot(snapshot):
	raw = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
	return base64.b64encode(zlib.compress(raw, 9)).decode("ascii")


def _decode_snapshot(encoded):
	raw = zlib.decompress(base64.b64decode(encoded.encode("ascii")))
	snapshot = json.loads(raw.decode("utf-8"))
	if snapshot.get("root") != REGISTRY_ROOT:
		raise ValueError("Snapshot is not for the Dolphin registry root")
	if int(snapshot.get("version", 0)) != SNAPSHOT_VERSION:
		raise ValueError("Unsupported Orpheus registry snapshot version")
	return snapshot


def _export_registry_to_profile():
	snapshot = _create_registry_snapshot()
	config.conf[CONF_SECTION][CONF_SNAPSHOT] = _encode_snapshot(snapshot)
	config.conf[CONF_SECTION][CONF_UPDATED] = snapshot["created"]
	config.conf.save()
	return snapshot


def _restore_registry_from_profile():
	encoded = config.conf[CONF_SECTION][CONF_SNAPSHOT]
	if not encoded:
		raise ValueError("No Orpheus registry snapshot is stored in the NVDA profile")
	snapshot = _decode_snapshot(encoded)
	_write_key(REGISTRY_ROOT, snapshot["data"])
	return snapshot


def _restore_registry_from_profile_if_enabled():
	try:
		_ensure_config()
		if not config.conf[CONF_SECTION][CONF_AUTO_RESTORE]:
			return
		if not config.conf[CONF_SECTION][CONF_SNAPSHOT]:
			return
		snapshot = _restore_registry_from_profile()
		log.debug("Restored Orpheus Classic registry snapshot from NVDA profile: %s" % snapshot.get("created", "unknown"))
	except Exception:
		log.exception("Failed to restore Orpheus Classic registry snapshot from NVDA profile")


def _get_orpheus_dir():
	return os.path.join(
		globalVars.appArgs.configPath,
		"addons",
		"orpheusClassic",
		"synthDrivers",
		"orpheusClassic",
		"orpheus",
	)


def _get_current_exception_language():
	try:
		synth = speech.speech.getSynth()
		if getattr(synth, "name", "") == "orpheusClassic":
			langs = synth._get_cached_langs()
			if not langs:
				synth._refresh_language_cache()
				langs = synth._get_cached_langs()
			voice = int(getattr(synth, "_voice", DEFAULT_EXCEPTION_LANGUAGE))
			for lang in langs:
				if int(lang.get("country", DEFAULT_EXCEPTION_LANGUAGE)) == voice:
					return voice
			return DEFAULT_EXCEPTION_LANGUAGE
	except Exception:
		log.debugWarning("Falling back to default Orpheus exception language", exc_info=True)
	return DEFAULT_EXCEPTION_LANGUAGE


def _get_exception_path(language=None):
	if language is None:
		language = _get_current_exception_language()
	return os.path.join(_get_orpheus_dir(), "settings", f"{int(language):05d}.exc")


def _backup_file(path):
	if not os.path.exists(path):
		return None
	stamp = time.strftime("%Y%m%d-%H%M%S")
	backup = f"{path}.{stamp}.bak"
	shutil.copy2(path, backup)
	return backup


def _normalise_native_exception(item):
	if not isinstance(item, dict):
		return None
	source = str(item.get("source", "") or "").strip()
	replacement = str(item.get("replacement", "") or "")
	match_type = str(item.get("matchType", EXCEPTION_MATCH_WHOLE_WORD) or EXCEPTION_MATCH_WHOLE_WORD)
	if match_type not in EXCEPTION_MATCH_TYPES:
		match_type = EXCEPTION_MATCH_WHOLE_WORD
	case_sensitive = bool(item.get("caseSensitive", False))
	if not source:
		return None
	return {
		"source": source,
		"replacement": replacement,
		"matchType": match_type,
		"caseSensitive": case_sensitive,
	}


def _sort_native_exceptions(entries):
	return sorted(
		entries,
		key=_native_exception_sort_key,
	)


def _native_exception_sort_key(item):
	return (
		item["source"].casefold(),
		item["replacement"].casefold(),
		item["matchType"],
		item["caseSensitive"],
	)


def _native_exception_source_key(item):
	return item["source"].casefold()


def _native_exception_pronunciation_key(item):
	return (
		item["source"].casefold(),
		item["replacement"].casefold(),
		item["caseSensitive"],
	)


def _native_data_dir():
	return os.path.join(globalVars.appArgs.configPath, DATA_DIR_NAME)


def _native_dictionary_path():
	return os.path.join(_native_data_dir(), DICTIONARY_FILE_NAME)


def _bundled_dictionary_path():
	addon_dir = os.path.dirname(os.path.dirname(__file__))
	return os.path.join(addon_dir, "synthDrivers", "orpheusClassic", DEFAULT_DICTIONARY_FILE_NAME)


def _decode_orpheus_text(value):
	def replace(match):
		try:
			return chr(int(match.group(1), 16))
		except Exception:
			return match.group(0)
	return re.sub(r"\\([0-9A-Fa-f]{4})", replace, value)


def _get_native_exceptions():
	_ensure_config()
	path = _native_dictionary_path()
	if not os.path.exists(path):
		path = _bundled_dictionary_path()
		if not os.path.exists(path):
			return []
	try:
		with open(path, "r", encoding="utf-8-sig") as f:
			data = json.load(f)
	except Exception:
		log.debugWarning("Ignoring invalid Orpheus Classic dictionary file", exc_info=True)
		return []
	if isinstance(data, dict):
		items = data.get("entries", [])
	else:
		items = data
	if not isinstance(items, list):
		return []
	result = []
	seen = set()
	for item in items:
		normalised = _normalise_native_exception(item)
		if normalised is not None and _native_exception_sort_key(normalised) not in seen:
			seen.add(_native_exception_sort_key(normalised))
			result.append(normalised)
	return _sort_native_exceptions(result)


def _set_native_exceptions(entries):
	_ensure_config()
	clean = []
	seen = set()
	for item in entries:
		normalised = _normalise_native_exception(item)
		if normalised is not None and _native_exception_sort_key(normalised) not in seen:
			seen.add(_native_exception_sort_key(normalised))
			clean.append(normalised)
	clean = _sort_native_exceptions(clean)
	os.makedirs(_native_data_dir(), exist_ok=True)
	with open(_native_dictionary_path(), "w", encoding="utf-8") as f:
		json.dump({"entries": clean}, f, ensure_ascii=False, indent=2, sort_keys=True)


def _load_native_exceptions_file(path):
	with open(path, "r", encoding="utf-8-sig") as f:
		raw = f.read()
	if path.lower().endswith(".json"):
		data = json.loads(raw)
		if isinstance(data, dict):
			data = data.get("entries", [])
		if not isinstance(data, list):
			raise ValueError("Expected a list of dictionary entries")
		return [_normalise_native_exception(item) for item in data if _normalise_native_exception(item) is not None]
	entries = []
	for line in raw.splitlines():
		line = line.strip()
		if not line or line.startswith("#"):
			continue
		parts = line.split("\t")
		if len(parts) >= 3 and parts[2].strip().upper() in ORPHEUS_TEXT_FLAG_MAP:
			source = _decode_orpheus_text(parts[0].strip())
			replacement = _decode_orpheus_text(parts[1])
			match_type = ORPHEUS_TEXT_FLAG_MAP[parts[2].strip().upper()]
			normalised = _normalise_native_exception({
				"source": source,
				"replacement": replacement,
				"matchType": match_type,
				"caseSensitive": False,
			})
			if normalised is not None:
				entries.append(normalised)
			continue
		if "\t" in line:
			source, replacement = line.split("\t", 1)
		elif "=" in line:
			source, replacement = line.split("=", 1)
		else:
			raise ValueError("Text dictionaries must use tab-separated or source=replacement lines")
		normalised = _normalise_native_exception({"source": source, "replacement": replacement})
		if normalised is not None:
			entries.append(normalised)
	return entries


def _encode_orpheus_text(value):
	result = []
	for ch in value:
		code = ord(ch)
		if code > 0x7F:
			result.append("\\%04x" % code)
		else:
			result.append(ch)
	return "".join(result)


def _entry_to_orpheus_text_line(entry):
	if entry["matchType"] == EXCEPTION_MATCH_REGEX:
		return None
	flag_map = {
		EXCEPTION_MATCH_WHOLE_WORD: "W",
		EXCEPTION_MATCH_ANYWHERE: "A",
		EXCEPTION_MATCH_STARTS_WITH: "B",
		EXCEPTION_MATCH_ENDS_WITH: "E",
	}
	flag = flag_map.get(entry["matchType"])
	if flag is None:
		return None
	return "%s\t%s\t%s" % (
		_encode_orpheus_text(entry["source"]),
		_encode_orpheus_text(entry["replacement"]),
		flag,
	)


def _write_native_exceptions_file(path, entries):
	entries = _sort_native_exceptions([
		normalised for normalised in (_normalise_native_exception(item) for item in entries)
		if normalised is not None
	])
	if path.lower().endswith(".txt"):
		lines = []
		skipped = 0
		for entry in entries:
			line = _entry_to_orpheus_text_line(entry)
			if line is None:
				skipped += 1
				continue
			lines.append(line)
		with open(path, "w", encoding="utf-8", newline="\r\n") as f:
			f.write("\n".join(lines))
			if lines:
				f.write("\n")
		return skipped
	with open(path, "w", encoding="utf-8") as f:
		json.dump({"entries": entries}, f, ensure_ascii=False, indent=2)
	return 0


def _read_u16(data, offset):
	return int.from_bytes(data[offset:offset + 2], "little")


def _read_legacy_exception_entries(path):
	data = open(path, "rb").read()
	if len(data) < 0x140:
		raise ValueError("Exceptions file is too small")
	alphabet_offset = int.from_bytes(data[0x84:0x88], "little")
	data_offset = int.from_bytes(data[0x8C:0x90], "little")
	if alphabet_offset <= 0 or data_offset <= 0:
		raise ValueError("Exceptions file header is not recognised")
	count = int.from_bytes(data[alphabet_offset:alphabet_offset + 4], "little")
	alphabet_start = alphabet_offset + 4
	alphabet_end = alphabet_start + count * 2
	if count <= 0 or alphabet_end > len(data):
		raise ValueError("Exceptions alphabet table is not recognised")
	alphabet = [
		data[alphabet_start + i * 2:alphabet_start + i * 2 + 2].decode("utf-16le")
		for i in range(count)
	]
	entries = []
	bucket_index = 0
	tokens = []
	offset = data_offset
	while offset + 2 <= len(data) and bucket_index < len(alphabet):
		code = _read_u16(data, offset)
		if code == 0xE7FF:
			_entries_from_legacy_tokens(entries, alphabet[bucket_index], tokens)
			tokens = []
			bucket_index += 1
			offset += 2
			continue
		if code == 0xE720:
			offset += 2
			if offset + 2 > len(data):
				break
			code = _read_u16(data, offset)
		if 0xE800 <= code <= 0xE8FF:
			length = code - 0xE800
			offset += 2
			end = offset + length * 2
			if end > len(data):
				break
			tokens.append(data[offset:end].decode("utf-16le", "replace"))
			offset = end
			continue
		offset += 2
	if bucket_index < len(alphabet):
		_entries_from_legacy_tokens(entries, alphabet[bucket_index], tokens)
	return entries


def _entries_from_legacy_tokens(entries, prefix, tokens):
	for index in range(0, len(tokens) - 1, 2):
		source = prefix + tokens[index]
		replacement = tokens[index + 1]
		normalised = _normalise_native_exception({"source": source, "replacement": replacement})
		if normalised is not None:
			entries.append(normalised)


def _load_current_legacy_exception_entries():
	path = _get_exception_path()
	if not os.path.exists(path):
		return []
	return _read_legacy_exception_entries(path)


class ClassicExceptionDialog(wx.Dialog):
	def __init__(
		self,
		parent,
		title,
		source="",
		replacement="",
		matchType=EXCEPTION_MATCH_WHOLE_WORD,
		caseSensitive=False,
	):
		super().__init__(parent, title=title)
		main = wx.BoxSizer(wx.VERTICAL)
		sourceLabel = wx.StaticText(self, label=_("&Text to match:"))
		main.Add(sourceLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
		self.sourceCtrl = wx.TextCtrl(self)
		self.sourceCtrl.SetName(_("Text to match"))
		self.sourceCtrl.SetValue(source)
		main.Add(self.sourceCtrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
		replacementLabel = wx.StaticText(self, label=_("&Replacement text:"))
		main.Add(replacementLabel, 0, wx.LEFT | wx.RIGHT, 10)
		self.replacementCtrl = wx.TextCtrl(self)
		self.replacementCtrl.SetName(_("Replacement text"))
		self.replacementCtrl.SetValue(replacement)
		main.Add(self.replacementCtrl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
		matchLabel = wx.StaticText(self, label=_("&Match type:"))
		main.Add(matchLabel, 0, wx.LEFT | wx.RIGHT, 10)
		self.matchTypeChoice = wx.Choice(
			self,
			choices=[MATCH_TYPE_LABELS[value] for value in MATCH_TYPE_VALUES],
		)
		try:
			self.matchTypeChoice.SetSelection(MATCH_TYPE_VALUES.index(matchType))
		except ValueError:
			self.matchTypeChoice.SetSelection(0)
		main.Add(self.matchTypeChoice, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
		self.caseSensitiveCtrl = wx.CheckBox(self, label=_("&Case sensitive"))
		self.caseSensitiveCtrl.SetValue(bool(caseSensitive))
		main.Add(self.caseSensitiveCtrl, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
		buttonRow = wx.BoxSizer(wx.HORIZONTAL)
		self.testButton = wx.Button(self, label=_("&Test"))
		buttonRow.Add(self.testButton, 0, wx.RIGHT, 6)
		buttons = self.CreateButtonSizer(wx.OK | wx.CANCEL)
		buttonRow.Add(buttons, 0)
		main.Add(buttonRow, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
		self.SetSizerAndFit(main)
		self.testButton.Bind(wx.EVT_BUTTON, self.onTest)
		self.sourceCtrl.SetFocus()

	def getValues(self):
		selection = self.matchTypeChoice.GetSelection()
		if selection == wx.NOT_FOUND:
			selection = 0
		return {
			"source": self.sourceCtrl.GetValue().strip(),
			"replacement": self.replacementCtrl.GetValue(),
			"matchType": MATCH_TYPE_VALUES[selection],
			"caseSensitive": bool(self.caseSensitiveCtrl.GetValue()),
		}

	def TransferDataFromWindow(self):
		entry = self.getValues()
		if not entry["source"]:
			gui.messageBox(
				_("Enter text to match."),
				_("Orpheus Classic"),
				wx.OK | wx.ICON_WARNING,
				parent=self,
			)
			self.sourceCtrl.SetFocus()
			return False
		return True

	def onTest(self, evt):
		entry = self.getValues()
		if not entry["source"]:
			ui.message(_("Enter text to match before testing."))
			self.sourceCtrl.SetFocus()
			return
		text = entry["replacement"] if entry["replacement"] else entry["source"]
		queueHandler.queueFunction(queueHandler.eventQueue, speech.speakMessage, text)


class OrpheusClassicSettingsPanel(gui.settingsDialogs.SettingsPanel):
	title = _("Orpheus Classic")

	def makeSettings(self, settingsSizer):
		_ensure_config()
		helper = gui.guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
		self.registryStatus = helper.addItem(wx.StaticText(
			self,
			label=self._registry_status_text(),
		))
		self.autoRestore = helper.addItem(wx.CheckBox(
			self,
			label=_("Restore saved Dolphin registry settings when NVDA starts"),
		))
		self.autoRestore.SetValue(bool(config.conf[CONF_SECTION][CONF_AUTO_RESTORE]))
		registryButtons = wx.BoxSizer(wx.HORIZONTAL)
		self.saveRegistryButton = wx.Button(self, label=_("Save registry &settings"))
		self.restoreRegistryButton = wx.Button(self, label=_("Rest&ore registry settings"))
		registryButtons.Add(self.saveRegistryButton, 0, wx.RIGHT, 6)
		registryButtons.Add(self.restoreRegistryButton, 0, wx.RIGHT, 6)
		helper.addItem(registryButtons)
		self.saveRegistryButton.Bind(wx.EVT_BUTTON, self.onSaveRegistry)
		self.restoreRegistryButton.Bind(wx.EVT_BUTTON, self.onRestoreRegistry)
		helper.addItem(wx.StaticLine(self))
		helper.addItem(wx.StaticText(
			self,
			label=_("Dictionary replacements are stored in a separate Orpheus Classic dictionary file under NVDA's configuration folder and are applied before text is sent to Orpheus. Use Import old .exc to copy entries from the old Orpheus exception file into this editor."),
		))
		helper.addItem(wx.StaticText(
			self,
			label=_("Dictionary file: %s") % _native_dictionary_path(),
		))
		dictionaryLabel = helper.addItem(wx.StaticText(self, label=_("&Dictionary entries:")))
		self.entriesList = helper.addItem(wx.ListCtrl(
			self,
			style=wx.LC_REPORT | wx.BORDER_SUNKEN | wx.LC_SINGLE_SEL,
		))
		dictionaryLabel.SetName(_("Dictionary entries"))
		self.entriesList.SetName(_("Dictionary entries"))
		self.entriesList.InsertColumn(0, _("Text"), width=220)
		self.entriesList.InsertColumn(1, _("Replacement"), width=300)
		self.entriesList.InsertColumn(2, _("Match type"), width=160)
		self.entriesList.InsertColumn(3, _("Case sensitive"), width=120)
		buttonRow = wx.BoxSizer(wx.HORIZONTAL)
		self.addButton = wx.Button(self, label=_("&Add..."))
		self.editButton = wx.Button(self, label=_("&Edit..."))
		self.removeButton = wx.Button(self, label=_("&Remove"))
		self.testButton = wx.Button(self, label=_("&Test"))
		self.importLegacyButton = wx.Button(self, label=_("Import old .e&xc"))
		self.importButton = wx.Button(self, label=_("&Import dictionary file..."))
		self.exportButton = wx.Button(self, label=_("E&xport..."))
		self.updateButton = wx.Button(self, label=_("Check for &updates"))
		if WebManifestUpdater is None:
			self.updateButton.Disable()
		for button in (self.addButton, self.editButton, self.removeButton, self.testButton, self.importLegacyButton, self.importButton, self.exportButton, self.updateButton):
			buttonRow.Add(button, 0, wx.RIGHT, 6)
		helper.addItem(buttonRow)
		self.entries = _get_native_exceptions()
		if not self.entries:
			try:
				self.entries = _load_current_legacy_exception_entries()
			except Exception:
				log.debugWarning("Failed to pre-load Orpheus legacy exceptions", exc_info=True)
		self.entries = _sort_native_exceptions(self.entries)
		self._refresh_entries()
		self.addButton.Bind(wx.EVT_BUTTON, self.onAdd)
		self.editButton.Bind(wx.EVT_BUTTON, self.onEdit)
		self.removeButton.Bind(wx.EVT_BUTTON, self.onRemove)
		self.testButton.Bind(wx.EVT_BUTTON, self.onTest)
		self.importLegacyButton.Bind(wx.EVT_BUTTON, self.onImportLegacy)
		self.importButton.Bind(wx.EVT_BUTTON, self.onImport)
		self.exportButton.Bind(wx.EVT_BUTTON, self.onExport)
		self.updateButton.Bind(wx.EVT_BUTTON, self.onCheckUpdates)
		self.entriesList.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.onEdit)
		self.Bind(wx.EVT_CHAR_HOOK, self.onCharHook)

	def onCharHook(self, evt):
		if evt.AltDown() and evt.GetKeyCode() == ord("D"):
			self.entriesList.SetFocus()
			return
		evt.Skip()

	def _registry_status_text(self):
		updated = config.conf[CONF_SECTION].get(CONF_UPDATED, "") or _("never")
		return _("Saved Dolphin registry settings: %s") % updated

	def _refresh_entries(self, selected=None):
		self.entries = _sort_native_exceptions(self.entries)
		self.entriesList.DeleteAllItems()
		for entry in self.entries:
			index = self.entriesList.InsertItem(self.entriesList.GetItemCount(), entry["source"])
			self.entriesList.SetItem(index, 1, entry["replacement"])
			self.entriesList.SetItem(index, 2, MATCH_TYPE_LABELS.get(entry["matchType"], _("Whole word")))
			self.entriesList.SetItem(index, 3, _("Yes") if entry["caseSensitive"] else _("No"))
		if isinstance(selected, dict):
			selected = self._find_entry_index(selected)
		if selected is not None and 0 <= selected < len(self.entries):
			self.entriesList.Select(selected)
			self.entriesList.Focus(selected)

	def _find_entry_index(self, target):
		target_key = _native_exception_sort_key(target)
		for index, entry in enumerate(self.entries):
			if _native_exception_sort_key(entry) == target_key:
				return index
		return None

	def _selected_index(self):
		index = self.entriesList.GetFirstSelected()
		return index if index != -1 else None

	def onAdd(self, evt):
		dialog = ClassicExceptionDialog(self, _("Add Orpheus Classic dictionary entry"))
		try:
			if dialog.ShowModal() == wx.ID_OK:
				entry = _normalise_native_exception(dialog.getValues())
				if entry is not None:
					self.entries.append(entry)
					self._refresh_entries(entry)
		finally:
			dialog.Destroy()

	def onEdit(self, evt):
		index = self._selected_index()
		if index is None:
			ui.message(_("Select a dictionary entry first."))
			return
		entry = self.entries[index]
		dialog = ClassicExceptionDialog(
			self,
			_("Edit Orpheus Classic dictionary entry"),
			source=entry["source"],
			replacement=entry["replacement"],
			matchType=entry["matchType"],
			caseSensitive=entry["caseSensitive"],
		)
		try:
			if dialog.ShowModal() == wx.ID_OK:
				updated = _normalise_native_exception(dialog.getValues())
				if updated is not None:
					self.entries[index] = updated
					self._refresh_entries(updated)
		finally:
			dialog.Destroy()

	def onRemove(self, evt):
		index = self._selected_index()
		if index is None:
			ui.message(_("Select a dictionary entry first."))
			return
		del self.entries[index]
		self._refresh_entries(min(index, len(self.entries) - 1))

	def onTest(self, evt):
		index = self._selected_index()
		if index is None:
			ui.message(_("Select a dictionary entry first."))
			return
		entry = self.entries[index]
		text = entry["replacement"] if entry["replacement"] else entry["source"]
		queueHandler.queueFunction(queueHandler.eventQueue, speech.speakMessage, text)

	def onImportLegacy(self, evt):
		try:
			imported = _load_current_legacy_exception_entries()
		except Exception:
			log.exception("Failed to import Orpheus legacy exceptions")
			gui.messageBox(
				_("The current Orpheus .exc file could not be imported."),
				_("Orpheus Classic"),
				wx.OK | wx.ICON_ERROR,
				parent=self,
			)
			return
		if not imported:
			ui.message(_("No legacy Orpheus exceptions were found."))
			return
		self._merge_imported_entries(imported, _("Import old Orpheus .exc"))

	def onImport(self, evt):
		with wx.FileDialog(
			self,
			_("Import Orpheus Classic dictionary file"),
			wildcard=_("Dictionary files (*.json;*.txt)|*.json;*.txt|All files (*.*)|*.*"),
			style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
		) as dialog:
			if dialog.ShowModal() != wx.ID_OK:
				return
			path = dialog.GetPath()
		try:
			imported = _load_native_exceptions_file(path)
		except Exception:
			log.exception("Failed to import Orpheus Classic dictionary")
			gui.messageBox(
				_("The selected dictionary could not be imported."),
				_("Orpheus Classic"),
				wx.OK | wx.ICON_ERROR,
				parent=self,
			)
			return
		self._merge_imported_entries(imported, _("Import Orpheus Classic dictionary"))

	def _merge_imported_entries(self, imported, title):
		clean_imported = []
		imported_exact = set()
		for normalised in (_normalise_native_exception(item) for item in imported):
			if normalised is None:
				continue
			exact_key = _native_exception_sort_key(normalised)
			if exact_key in imported_exact:
				continue
			imported_exact.add(exact_key)
			clean_imported.append(normalised)
		imported = clean_imported
		if not imported:
			ui.message(_("No dictionary entries were found to import."))
			return
		existing_exact = {_native_exception_sort_key(item) for item in self.entries}
		existing_sources = {_native_exception_source_key(item) for item in self.entries}
		existing_pronunciations = {
			_native_exception_pronunciation_key(item): item
			for item in self.entries
		}
		exact_duplicates = []
		match_type_updates = []
		new_sources = []
		same_source_different_pronunciation = []
		for item in imported:
			exact_key = _native_exception_sort_key(item)
			if exact_key in existing_exact:
				exact_duplicates.append(item)
			elif _native_exception_pronunciation_key(item) in existing_pronunciations:
				match_type_updates.append(item)
			elif _native_exception_source_key(item) in existing_sources:
				same_source_different_pronunciation.append(item)
			else:
				new_sources.append(item)
		for item in match_type_updates:
			existing = existing_pronunciations[_native_exception_pronunciation_key(item)]
			existing["matchType"] = item["matchType"]
		if not new_sources and not same_source_different_pronunciation:
			self._refresh_entries(match_type_updates[0] if match_type_updates else None)
			ui.message(_(
				"No new dictionary entries were imported. %d exact duplicates were skipped. %d existing entries were updated with more specific match types."
			) % (len(exact_duplicates), len(match_type_updates)))
			return
		if same_source_different_pronunciation:
			message = _(
				"%d exact duplicates will be skipped.\n"
				"%d existing entries will be updated with more specific match types.\n"
				"%d entries are for words not already in the dictionary.\n"
				"%d entries are for existing words but have a different pronunciation.\n\n"
				"What do you want to import?"
			) % (len(exact_duplicates), len(match_type_updates), len(new_sources), len(same_source_different_pronunciation))
			dialog = wx.MessageDialog(
				self,
				message,
				title,
				wx.YES_NO | wx.CANCEL | wx.ICON_QUESTION,
			)
			dialog.SetYesNoCancelLabels(
				_("New words only"),
				_("All non-duplicates"),
				_("Cancel"),
			)
			try:
				result = dialog.ShowModal()
			finally:
				dialog.Destroy()
			if result == wx.ID_CANCEL:
				ui.message(_("Import cancelled."))
				return
			to_add = list(new_sources)
			if result == wx.ID_NO:
				to_add.extend(same_source_different_pronunciation)
		else:
			to_add = list(new_sources)
		for item in to_add:
			self.entries.append(item)
		self._refresh_entries((to_add or match_type_updates)[0] if (to_add or match_type_updates) else None)
		ui.message(_(
			"Imported %d dictionary entries. %d exact duplicates were skipped. %d existing entries were updated with more specific match types."
		) % (len(to_add), len(exact_duplicates), len(match_type_updates)))

	def onExport(self, evt):
		with wx.FileDialog(
			self,
			_("Export Orpheus Classic dictionary"),
			defaultFile="orpheusClassicDictionary.json",
			wildcard=_("JSON files (*.json)|*.json|Orpheus text dictionary (*.txt)|*.txt|All files (*.*)|*.*"),
			style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
		) as dialog:
			if dialog.ShowModal() != wx.ID_OK:
				return
			path = dialog.GetPath()
		try:
			skipped = _write_native_exceptions_file(path, self.entries)
		except Exception:
			log.exception("Failed to export Orpheus Classic dictionary")
			gui.messageBox(
				_("The dictionary could not be exported."),
				_("Orpheus Classic"),
				wx.OK | wx.ICON_ERROR,
				parent=self,
			)
			return
		if skipped:
			ui.message(_("Orpheus Classic dictionary exported. %d regular expression entries were skipped because the Orpheus text format does not support them.") % skipped)
		else:
			ui.message(_("Orpheus Classic dictionary exported."))

	def onCheckUpdates(self, evt):
		updater = getattr(self, "updater", None) or CURRENT_UPDATER
		if updater is None:
			ui.message(_("The Orpheus Classic updater is not available."))
			return
		wx.CallAfter(updater.checkNow, True)

	def onSaveRegistry(self, evt):
		try:
			snapshot = _export_registry_to_profile()
		except FileNotFoundError:
			ui.message(_("No Dolphin Orpheus registry settings were found to save."))
			return
		except Exception:
			log.exception("Failed to save Orpheus Classic registry snapshot to NVDA profile")
			ui.message(_("Failed to save Orpheus Classic settings to the NVDA profile."))
			return
		self.registryStatus.SetLabel(self._registry_status_text())
		ui.message(_("Orpheus Classic registry settings saved."))
		log.info("Saved Orpheus Classic registry snapshot to NVDA profile: %s" % snapshot.get("created", "unknown"))

	def onRestoreRegistry(self, evt):
		try:
			snapshot = _restore_registry_from_profile()
		except Exception:
			log.exception("Failed to restore Orpheus Classic registry snapshot from NVDA profile")
			ui.message(_("Failed to restore Orpheus Classic settings from the NVDA profile."))
			return
		ui.message(_("Orpheus Classic registry settings restored."))
		log.info("Restored Orpheus Classic registry snapshot from NVDA profile: %s" % snapshot.get("created", "unknown"))

	def onSave(self):
		config.conf[CONF_SECTION][CONF_AUTO_RESTORE] = bool(self.autoRestore.GetValue())
		_set_native_exceptions(self.entries)


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	def __init__(self):
		super().__init__()
		global CURRENT_UPDATER
		self._updater = None
		_ensure_config()
		_restore_registry_from_profile_if_enabled()
		if globalVars.appArgs.secure:
			return
		if cleanupStaleHelpers:
			cleanupStaleHelpers("orpheusClassic")
		if WebManifestUpdater:
			manifestUrl = UPDATE_MANIFEST_TEST_URL if config.conf[CONF_SECTION].get(UPDATE_TEST_FLAG, False) else UPDATE_MANIFEST_URL
			self._updater = WebManifestUpdater("orpheusClassic", "Orpheus Classic", manifestUrl)
			CURRENT_UPDATER = self._updater
			self._updater.start()
		if OrpheusClassicSettingsPanel not in gui.settingsDialogs.NVDASettingsDialog.categoryClasses:
			gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(OrpheusClassicSettingsPanel)
		tools_menu = gui.mainFrame.sysTrayIcon.toolsMenu
		self.settings = tools_menu.Append(wx.ID_ANY, _("Orpheus Class&ic settings..."))
		gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self.on_settings, self.settings)

	def on_settings(self, evt):
		def showSettings():
			dialog = gui.mainFrame._popupSettingsDialog(gui.settingsDialogs.NVDASettingsDialog, OrpheusClassicSettingsPanel)
			try:
				panel = dialog.currentCategory
				if isinstance(panel, OrpheusClassicSettingsPanel):
					panel.updater = self._updater
			except Exception:
				pass
		wx.CallAfter(showSettings)

	def terminate(self):
		global CURRENT_UPDATER
		if globalVars.appArgs.secure:
			return
		if self._updater:
			self._updater.stop()
		CURRENT_UPDATER = None
		tools_menu = gui.mainFrame.sysTrayIcon.toolsMenu
		try:
			gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(OrpheusClassicSettingsPanel)
		except ValueError:
			pass
		tools_menu.RemoveItem(self.settings.Id)

