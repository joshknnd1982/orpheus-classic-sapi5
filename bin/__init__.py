import os
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), "orpheus"))
import subprocess
import threading
import json
import re
from io import StringIO
import struct
import time
from collections import namedtuple
import shutil
import socket

from synthDriverHandler import synthDoneSpeaking, SynthDriver, synthIndexReached, VoiceInfo
from speech.commands import PitchCommand, IndexCommand, LangChangeCommand
from autoSettingsUtils.driverSetting import NumericDriverSetting
import addonHandler
import config
import nvwave

addonHandler.initTranslation()

METADATA_COMMAND_TIMEOUT = 1.0
HOST_CONNECT_TIMEOUT = 10.0
HOST_INIT_TIMEOUT = 30.0
DEFAULT_LANGUAGE = "en-gb"
FALLBACK_PITCH_MIN = 50
FALLBACK_PITCH_MAX = 500
FALLBACK_DEFAULT_PITCH = 110
BUILD18_RATE_MIN = 40
BUILD18_RATE_DEFAULT = 110
BUILD18_RATE_MAX = 700
CONF_SECTION = "orpheusClassic"
CONF_PARAMETER_OVERRIDES = "parameterOverrides"
DATA_DIR_NAME = "orpheusClassic"
DICTIONARY_FILE_NAME = "dictionary.json"
DEFAULT_DICTIONARY_FILE_NAME = "defaultDictionary.json"
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
BUILD18_DEFAULT_VOICING = 65
BUILD18_DEFAULT_VOICE_SOURCE = 0
PARAMETER_SETTINGS = (
	("intonation", ("prosody", "intonation"), _("&Intonation"), 0, 100, 50, 1, 5, 10, _("Intonation"), 2),
	("voicing", ("voicing",), _("&Voicing"), 0, 100, BUILD18_DEFAULT_VOICING, 1, 5, 10, _("Voicing"), 5),
	("voiceSource", ("voice", "source"), _("Voice s&ource"), 0, 1, BUILD18_DEFAULT_VOICE_SOURCE, 1, 1, 1, _("Voice source"), 12),
	("wordPauseAmount", ("word", "pause"), _("&Word pause"), 0, 100, 0, 1, 5, 10, _("Word pause"), 3),
	("phrasePauseAmount", ("phrase", "pause"), _("P&hrase pause"), 0, 100, 12, 1, 5, 10, _("Phrase pause"), 4),
)
LEGACY_VOICE_INDEX_TO_COUNTRY = {
	"0": 1,
	"1": 31,
	"2": 33,
	"3": 34,
	"4": 39,
	"5": 44,
	"6": 46,
	"7": 49,
	"8": 52,
}
LANGUAGES = {
	1: 'en',
	44: 'en-gb',
	30: 'el',
	31: 'nl',
	33: 'fr',
	34: 'es',
	36: 'hu',
	38: 'hr',
	39: 'it',
	40: 'ro',
	42: 'cs',
	45: 'da',
	46: 'sv',
	47: 'nb_NO',
	48: 'pl',
	49: 'de',
	52: 'es-mx',
	55: 'pt-br',
	60: 'ms',
	86: 'zh',
	351: 'pt-pt',
	358: 'fi',
	370: 'lt',
	10044: 'cy',
	10086: 'zh',
}

ParamDesc = namedtuple('ParamDesc', ['min', 'max', 'name', 'current'])

FRAME_COMMAND = 1
FRAME_RESPONSE = 2
FRAME_EVENT = 3
CMD_INITIALIZE = 1
CMD_APPEND = 2
CMD_SPEAK_APPEND = 3
CMD_MUTE = 4
CMD_CONFIG = 5
CMD_GET_PARAMS = 6
CMD_GET_LANGS = 7
CMD_GET_VOICES = 8
CMD_CLOSE = 9
EV_AUDIO = 1
EV_DONE = 2
STATUS_OK = 0

COMMAND_IDS = {
	"initialize": CMD_INITIALIZE,
	"append": CMD_APPEND,
	"speakAppend": CMD_SPEAK_APPEND,
	"mute": CMD_MUTE,
	"config": CMD_CONFIG,
	"getParams": CMD_GET_PARAMS,
	"getLangs": CMD_GET_LANGS,
	"getVoices": CMD_GET_VOICES,
	"close": CMD_CLOSE,
}

def _ensure_native_exception_config():
	if CONF_SECTION not in config.conf.spec:
		config.conf.spec[CONF_SECTION] = {}
	config.conf.spec[CONF_SECTION].setdefault(CONF_PARAMETER_OVERRIDES, "string(default='{}')")
	_ = config.conf[CONF_SECTION]


def _native_dictionary_path():
	base = getattr(config, "getUserDefaultConfigPath", None)
	if callable(base):
		config_path = base()
	else:
		config_path = getattr(config, "confDir", None) or os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "nvda")
	return os.path.join(config_path, DATA_DIR_NAME, DICTIONARY_FILE_NAME)


def _bundled_dictionary_path():
	return os.path.join(os.path.dirname(__file__), DEFAULT_DICTIONARY_FILE_NAME)


def _active_dictionary_path():
	path = _native_dictionary_path()
	if os.path.exists(path):
		return path
	path = _bundled_dictionary_path()
	if os.path.exists(path):
		return path
	return None


def _load_native_exceptions():
	try:
		_ensure_native_exception_config()
	except Exception:
		return []
	path = _active_dictionary_path()
	if not path:
		return []
	try:
		with open(path, "r", encoding="utf-8-sig") as f:
			data = json.load(f)
	except Exception:
		return []
	if isinstance(data, dict):
		items = data.get("entries", [])
	else:
		items = data
	if not isinstance(items, list):
		return []
	entries = []
	seen = set()
	for item in items:
		if not isinstance(item, dict):
			continue
		entry = _normalise_native_exception(item)
		if entry is not None:
			key = (
				entry["source"].casefold(),
				entry["replacement"].casefold(),
				entry["matchType"],
				entry["caseSensitive"],
			)
			if key in seen:
				continue
			seen.add(key)
			entries.append(entry)
	return entries


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


def _build_orpheus_settings():
	settings = [
		SynthDriver.RateSetting(),
		SynthDriver.VolumeSetting(),
		SynthDriver.PitchSetting(),
		SynthDriver.VoiceSetting(),
	]
	for key, _tokens, label, min_value, max_value, default, min_step, normal_step, large_step, display_name, _fallback_id in PARAMETER_SETTINGS:
		settings.append(NumericDriverSetting(
			key,
			label,
			availableInSettingsRing=True,
			defaultVal=default,
			minVal=min_value,
			maxVal=max_value,
			minStep=min_step,
			normalStep=normal_step,
			largeStep=large_step,
			displayName=display_name,
		))
	return tuple(settings)


def _parameter_commands_from_synth(synth):
	# Build 18 accepts raw @<p=n> commands, but its non-raw TTS_Append
	# parameter block does not behave like later Orpheus builds. Keep the block
	# minimal until each old parameter is verified independently.
	return []


def _raw_command_values_from_synth(synth):
	commands = []
	voice = _countryToBuild18LanguageValue(getattr(synth, "_voice", 44))
	commands.append((9, voice))
	commands.append((0, getattr(synth, "_rate", BUILD18_RATE_DEFAULT)))
	commands.append((1, getattr(synth, "_pitch", FALLBACK_DEFAULT_PITCH)))
	commands.append((2, getattr(synth, "_intonation", 50)))
	commands.append((5, getattr(synth, "_voicing", BUILD18_DEFAULT_VOICING)))
	commands.append((3, synth._parameterSettingToOrpheusValue(
		"wordPauseAmount",
		getattr(synth, "wordPauseAmount", 0),
	)))
	commands.append((4, synth._parameterSettingToOrpheusValue(
		"phrasePauseAmount",
		getattr(synth, "phrasePauseAmount", 12),
	)))
	commands.append((7, getattr(synth, "_volume", 100)))
	commands.append((12, getattr(synth, "_voiceSource", BUILD18_DEFAULT_VOICE_SOURCE)))
	return tuple((param, int(value)) for param, value in commands)


def _raw_command_prefix(synth):
	return "".join(
		"@<%d=%d>" % (param, value)
		for param, value in _raw_command_values_from_synth(synth)
	)

def _countryToBuild18LanguageValue(country):
	try:
		country = int(country)
	except Exception:
		country = 44
	if country in (1, 31, 33, 34, 39, 44, 46, 49, 52):
		return country
	return 44


def _voiceIdFromCountry(country):
	try:
		country = int(country)
	except Exception:
		country = 44
	return "lang%d" % country


def _countryFromVoiceId(voice):
	voice = str(voice or "").strip()
	if voice.startswith("lang"):
		voice = voice[4:]
	try:
		country = int(voice)
	except Exception:
		country = None
	build18Countries = (1, 31, 33, 34, 39, 44, 46, 49, 52)
	if country in build18Countries:
		return country
	migrated = LEGACY_VOICE_INDEX_TO_COUNTRY.get(str(voice))
	if migrated is not None:
		return migrated
	return 44

class SynthDriver(SynthDriver):
	supportedSettings = _build_orpheus_settings()
	supportedCommands = {
		IndexCommand,
		PitchCommand,
		LangChangeCommand,
	}

	supportedNotifications = {synthIndexReached, synthDoneSpeaking}

	name='orpheusClassic'
	description='Orpheus Classic'

	@classmethod
	def check(cls):
		return True

	def __init__(self):
		self.lock = threading.Lock()
		self.queue = []
		self._response_events = {}
		self._next_msg_id = 1
		self._msg_lock = threading.Lock()
		self._send_lock = threading.Lock()
		self._process = None
		self._stop_reader = False
		self._langs = []
		self._voicesByCountry = {}
		self._initialIntonationConfigUntil = time.time() + 5
		
		self.event = threading.Event()
		
		try:
			output = config.conf["audio"]["outputDevice"]
		except:
			output = config.conf["speech"]["outputDevice"]
		self.player = nvwave.WavePlayer(2, 22050, 16, outputDevice=output)
		
		self.is_speaking = False
		self._last_audio_time = 0
		self._fallback_done_timer = None
		self._estimated_done_timer = None
		self._estimated_index_timers = []
		self._speech_run_id = 0
		self._pendingFinalIndex = 0
		self._currentSpeechText = ""
		self._dictionaryCacheSignature = None
		self._dictionaryCache = []
		
		self._start_host()
		
		# Cache Orpheus parameter descriptors (used for mapping NVDA percentages to
		# Orpheus parameter values).
		#
		# Important: Do NOT force the synth to Orpheus' internal default pitch here.
		# NVDA will apply the user's saved synth settings immediately after the driver
		# is instantiated. Setting pitch based on Orpheus' current value can override
		# NVDA's intended defaults and makes the pitch slider feel "ignored".
		self._paramDescs = self.get_params() or []
		self._pitchDesc = self._paramDescs[1] if len(self._paramDescs) > 1 else None
		self._pitchMin = self._pitchDesc.min if self._pitchDesc else FALLBACK_PITCH_MIN
		self._pitchMax = self._pitchDesc.max if self._pitchDesc else FALLBACK_PITCH_MAX
		self._defaultPitch = self._get_default_pitch()
		self._refresh_parameter_ids()
		self._refresh_language_cache()
		
		# Defaults (NVDA will override from config where applicable).
		self._volume = 100
		self._pitchPercent = 50
		self._pitch = self._pitchPercentToParam(self._pitchPercent)
		self._rate = BUILD18_RATE_DEFAULT
		self._voice = 44
		for key, _tokens, _label, _min_value, _max_value, default, _min_step, _normal_step, _large_step, _display_name, _fallback_id in PARAMETER_SETTINGS:
			setattr(self, "_" + key, default)
		self.index = 0
		self.last_reached = 0

		# Background worker for speech commands
		self._work_event = threading.Event()
		self._mute_pending = False
		self._terminating = False
		self._worker_thread = threading.Thread(target=self._worker_loop)
		self._worker_thread.daemon = True
		self._worker_thread.start()

	def initSettings(self):
		self._loadingSettings = True
		try:
			self._migrateLegacyVoiceSetting()
			super().initSettings()
			self._migrateLegacyPauseSettings()
		finally:
			self._loadingSettings = False

	def _start_host(self):
		host_exe = os.path.join(os.path.dirname(__file__), "orpheus-classic-host.exe")
		if not os.path.exists(host_exe):
			raise RuntimeError("Classic Orpheus host not found: " + host_exe)

		server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
		server.bind(("127.0.0.1", 0))
		server.listen(1)
		server.settimeout(HOST_CONNECT_TIMEOUT)
		address = server.getsockname()

		cmd = [host_exe, "--address", f"{address[0]}:{address[1]}"]
		
		startupinfo = subprocess.STARTUPINFO()
		startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
		
		self._process = subprocess.Popen(cmd, startupinfo=startupinfo)
		
		try:
			# Wait for connection with timeout
			self._conn, _peer = server.accept()
			self._conn.settimeout(1.0)
		except Exception:
			# If connection fails, ensure process is killed and re-raise
			if self._process.poll() is None:
				self._process.terminate()
			server.close()
			raise RuntimeError("Failed to connect to Orpheus host process.")
			
		server.close()
		
		self._reader_thread = threading.Thread(target=self._read_loop)
		self._reader_thread.start()
		
		orpheus_dir = os.path.join(os.path.dirname(__file__), 'orpheus')
		try:
			self._send_command("initialize", timeout=HOST_INIT_TIMEOUT, orpheus_dir=orpheus_dir)
		except RuntimeError:
			# If initialization fails, cleanup
			self.terminate()
			raise

	def _send_command(self, command, timeout=None, **payload):
		command_id = COMMAND_IDS[command]
		with self._msg_lock:
			msg_id = self._next_msg_id
			self._next_msg_id += 1
			evt = threading.Event()
			self._response_events[msg_id] = (evt, None)
		
		try:
			self._send_frame(self._build_command_frame(msg_id, command_id, payload))
			if not evt.wait(timeout):
				with self._msg_lock:
					self._response_events.pop(msg_id, None)
				raise RuntimeError("Timed out waiting for Orpheus host command: %s" % command)
			with self._msg_lock:
				try:
					_, response = self._response_events.pop(msg_id)
				except KeyError:
					# This can happen if cleanup removed it (though we stopped doing that)
					# or if something else went wrong.
					raise RuntimeError("Connection lost (event missing)")
			
			if isinstance(response, Exception):
				raise response
			return self._parse_response(command, response or b"")
		except Exception:
			# If sending fails, we should probably cleanup
			with self._msg_lock:
				if msg_id in self._response_events:
					del self._response_events[msg_id]
			raise

	def _build_command_frame(self, msg_id, command_id, payload):
		data = b""
		if command_id == CMD_INITIALIZE:
			path = payload["orpheus_dir"].encode("utf-8")
			data = struct.pack("<I", len(path)) + path
		elif command_id == CMD_APPEND:
			params = payload["parameters_bytes"]
			text = payload["text_bytes"]
			data = struct.pack("<II", len(params), len(text)) + params + text
		elif command_id == CMD_MUTE:
			data = struct.pack("<I", int(payload["val"]))
		elif command_id == CMD_GET_VOICES:
			data = struct.pack("<I", int(payload["country"]))
		return struct.pack("<BIH", FRAME_COMMAND, msg_id, command_id) + data

	def _send_frame(self, payload):
		frame = struct.pack("<I", len(payload)) + payload
		with self._send_lock:
			self._conn.sendall(frame)

	def _recv_exact(self, length):
		chunks = []
		remaining = length
		while remaining:
			try:
				chunk = self._conn.recv(remaining)
			except socket.timeout:
				if self._stop_reader:
					return None
				continue
			if not chunk:
				return None
			chunks.append(chunk)
			remaining -= len(chunk)
		return b"".join(chunks)

	def _recv_frame(self):
		header = self._recv_exact(4)
		if not header:
			return None
		length = struct.unpack("<I", header)[0]
		if length <= 0 or length > 64 * 1024 * 1024:
			return None
		return self._recv_exact(length)

	def _read_loop(self):
		while not self._stop_reader:
			try:
				frame = self._recv_frame()
				if frame is None:
					break
				frame_type = frame[0]
				if frame_type == FRAME_RESPONSE:
					msg_id, status = struct.unpack_from("<II", frame, 1)
					payload = frame[9:]
					with self._msg_lock:
						if msg_id in self._response_events:
							evt, _ = self._response_events[msg_id]
							if status != STATUS_OK:
								self._response_events[msg_id] = (evt, RuntimeError(payload.decode("utf-8", "replace")))
							else:
								self._response_events[msg_id] = (evt, payload)
							evt.set()
				elif frame_type == FRAME_EVENT:
					event = struct.unpack_from("<H", frame, 1)[0]
					self._handle_event(event, frame[3:])
			except Exception:
				# Connection broken or error
				break
		
		# Cleanup pending events to avoid deadlocks
		with self._msg_lock:
			for msg_id, (evt, _) in self._response_events.items():
				self._response_events[msg_id] = (evt, RuntimeError("Connection lost"))
				evt.set()
			# Do not clear, let the waiters pop their events

	def _handle_event(self, event, payload):
		if event == EV_AUDIO:
			offset = 0
			audio_len = struct.unpack_from("<I", payload, offset)[0]
			offset += 4
			audio_data = payload[offset:offset + audio_len]
			offset += audio_len
			controls_len = struct.unpack_from("<I", payload, offset)[0]
			offset += 4
			control_data = payload[offset:offset + controls_len]
			controls = list(struct.iter_unpack('III', control_data)) if controls_len else []
			self._on_audio({"audio": audio_data, "controls": controls})

	def _parse_response(self, command, payload):
		if command in ("initialize", "append", "speakAppend", "mute", "config", "close"):
			return "ok"
		if command == "getParams":
			return self._parse_params(payload)
		if command == "getLangs":
			return self._parse_langs(payload)
		if command == "getVoices":
			return self._parse_voices(payload)
		return payload

	def _read_string(self, payload, offset):
		length = struct.unpack_from("<I", payload, offset)[0]
		offset += 4
		value = payload[offset:offset + length].decode("utf-8", "replace")
		return value, offset + length

	def _parse_params(self, payload):
		offset = 0
		count = struct.unpack_from("<I", payload, offset)[0]
		offset += 4
		result = []
		for _i in range(count):
			minv, maxv, current = struct.unpack_from("<iii", payload, offset)
			offset += 12
			name, offset = self._read_string(payload, offset)
			result.append({"min": minv, "max": maxv, "name": name, "current": current})
		return result

	def _parse_langs(self, payload):
		offset = 0
		count = struct.unpack_from("<I", payload, offset)[0]
		offset += 4
		result = []
		for _i in range(count):
			country = struct.unpack_from("<I", payload, offset)[0]
			offset += 4
			lang, offset = self._read_string(payload, offset)
			name = None
			if offset + 4 <= len(payload):
				try:
					name, offset = self._read_string(payload, offset)
				except Exception:
					name = None
			result.append({"country": country, "lang": lang, "name": name or lang})
		return result

	def _parse_voices(self, payload):
		offset = 0
		count = struct.unpack_from("<I", payload, offset)[0]
		offset += 4
		result = []
		for _i in range(count):
			name, offset = self._read_string(payload, offset)
			result.append({"name": name})
		return result

	def _on_audio(self, payload):
		audio_data = payload["audio"]
		controls = payload["controls"]

		with self.lock:
			if not self.is_speaking:
				self.event.set()
				return
			run_id = self._speech_run_id

		if audio_data:
			self.player.feed(audio_data)
			self._last_audio_time = time.time()

		for pos, type, value in controls:
			if value & 0x80000000:
				index = value & 0x7FFFFFFF
				with self.lock:
					if not self.is_speaking or run_id != self._speech_run_id:
						return
					if self._fallback_done_timer is not None:
						self._fallback_done_timer.cancel()
						self._fallback_done_timer = None
					self.is_speaking = False
					self._cancel_estimated_index_timers()
				if index:
					synthIndexReached.notify(synth=self, index=index)
				synthDoneSpeaking.notify(synth=self)
				self._work_event.set()
			else:
				synthIndexReached.notify(synth=self, index=value)
		if not controls:
			self._schedule_fallback_done()

	def _estimate_speech_duration(self, text):
		text = re.sub(r"@<[^>]+>", " ", text or "")
		words = re.findall(r"\w+", text)
		word_count = max(1, len(words))
		rate = self._clampParam(getattr(self, "_rate", BUILD18_RATE_DEFAULT), BUILD18_RATE_MIN, BUILD18_RATE_MAX)
		wpm = 120 + ((rate - BUILD18_RATE_MIN) / float(BUILD18_RATE_MAX - BUILD18_RATE_MIN)) * 580
		duration = (word_count / max(80.0, wpm)) * 60.0
		duration += min(1.0, len(re.findall(r"[.,;:!?]", text)) * 0.04)
		return max(0.08, duration)

	def _schedule_estimated_done(self, index):
		if self._estimated_done_timer is not None:
			self._estimated_done_timer.cancel()
		delay = self._estimate_speech_duration(getattr(self, "_currentSpeechText", ""))
		self._estimated_done_timer = threading.Timer(delay, self._estimated_done, args=(index,))
		self._estimated_done_timer.daemon = True
		self._estimated_done_timer.start()

	def _cancel_estimated_index_timers(self):
		for timer in getattr(self, "_estimated_index_timers", []):
			try:
				timer.cancel()
			except Exception:
				pass
		self._estimated_index_timers = []

	def _schedule_estimated_indexes(self, parameters, text):
		self._cancel_estimated_index_timers()
		duration = self._estimate_speech_duration(text)
		text_len = max(1, len(text or ""))
		seen = set()
		timers = []
		with self.lock:
			run_id = self._speech_run_id
		for offset, _reserved, param_id, value in parameters:
			if param_id != 14 or (value & 0x80000000):
				continue
			index = int(value)
			if index <= 0 or index in seen:
				continue
			seen.add(index)
			fraction = max(0.0, min(1.0, float(offset) / text_len))
			delay = max(0.03, min(duration - 0.02, duration * fraction))
			timer = threading.Timer(delay, self._estimated_index_reached, args=(run_id, index))
			timer.daemon = True
			timer.start()
			timers.append(timer)
		self._estimated_index_timers = timers

	def _estimated_index_reached(self, run_id, index):
		with self.lock:
			if not self.is_speaking or run_id != self._speech_run_id:
				return
		synthIndexReached.notify(synth=self, index=index)

	def _estimated_done(self, index):
		with self.lock:
			if not self.is_speaking:
				return
			self.is_speaking = False
			self._estimated_done_timer = None
			self._cancel_estimated_index_timers()
		if index:
			synthIndexReached.notify(synth=self, index=index)
		synthDoneSpeaking.notify(synth=self)
		self._work_event.set()

	def _schedule_fallback_done(self):
		if self._fallback_done_timer is not None:
			self._fallback_done_timer.cancel()
		last_audio_time = self._last_audio_time
		self._fallback_done_timer = threading.Timer(2.0, self._fallback_done, args=(last_audio_time,))
		self._fallback_done_timer.daemon = True
		self._fallback_done_timer.start()

	def _fallback_done(self, audio_time):
		with self.lock:
			if not self.is_speaking or audio_time != self._last_audio_time:
				return
			self.is_speaking = False
			if self._fallback_done_timer is not None:
				self._fallback_done_timer.cancel()
				self._fallback_done_timer = None
			self._cancel_estimated_index_timers()
			index = getattr(self, "_pendingFinalIndex", 0)
		if index:
			synthIndexReached.notify(synth=self, index=index)
		synthDoneSpeaking.notify(synth=self)
		self._work_event.set()

	def speak(self, seq):
		# Ensure self._pitch and others are initialized. 
		# Although initialized in __init__ and updated in setters, extra safety doesn't hurt.
		# But since we fixed _set_pitch, relying on attributes is safe.
		
		parameters = []
		parameters.extend(_parameter_commands_from_synth(self))
		text = StringIO()
		text.write(_raw_command_prefix(self))
		text.write(' ')
		lastindex = 0
		for item in seq:
			if isinstance(item, str):
				text.write(self._apply_native_exceptions(item) + ' ')
			elif isinstance(item, IndexCommand):
				parameters.append((max(0, text.tell() - 1), 0, 14, item.index))
				text.write(' ')
				lastindex = item.index
			elif isinstance(item, LangChangeCommand):
				text.write(' ')
			elif isinstance(item, PitchCommand):
				if getattr(item, "isDefault", False):
					text.write("@<1=%d> " % int(getattr(self, "_pitch", FALLBACK_DEFAULT_PITCH)))
					continue
				# NVDA sends inline pitch changes as synth-parameter offsets.
				# Remapping them through the 0..100 slider range makes capitals
				# jump far too high; keep the 2025-era Orpheus behavior.
				text.write("@<1=%d> " % int(self._inlinePitchCommandToParam(item)))

		parameters.append((max(0, text.tell() - 1), 0, 14, lastindex | 0x80000000))
		self._pendingFinalIndex = lastindex
		
		with self.lock:
			self.queue.append((parameters, text.getvalue()))
		
		self._work_event.set()

	def _worker_loop(self):
		while not self._stop_reader:
			self._work_event.wait()
			if self._stop_reader:
				break
			
			item_to_process = None
			mute_pending = False
			
			with self.lock:
				if self._mute_pending:
					self._mute_pending = False
					mute_pending = True
				elif self.is_speaking:
					# Still speaking, wait for completion signal
					self._work_event.clear()
					continue
				
				if mute_pending:
					self._work_event.clear()
				elif self.queue:
					item_to_process = self.queue.pop(0)
					self.is_speaking = True
					self._speech_run_id += 1
				else:
					# Queue empty
					self._work_event.clear()
			
			if mute_pending:
				try:
					self._send_command("mute", timeout=0.15, val=3)
				except Exception:
					self._restart_stalled_host()
				self._work_event.set()
				continue

			if item_to_process:
				params, txt = item_to_process
				try:
					self._currentSpeechText = txt
					self.append(params, txt)
					self.speak_append()
					self._schedule_estimated_indexes(params, txt)
				except Exception:
					# If speak fails, reset speaking state so we don't hang
					with self.lock:
						self.is_speaking = False
						self._speech_run_id += 1
						self._cancel_estimated_index_timers()

	table = {
		ord("’"): ord("'"),
		ord("“"): ord('"'),
		ord("”"): ord('"'),
	}

	def _apply_native_exceptions(self, text):
		if not text:
			return text
		for pattern, replacement in self._get_dictionary_cache():
			try:
				text = pattern.sub(lambda _match, repl=replacement: repl, text)
			except Exception:
				continue
		return text

	def _get_dictionary_cache(self):
		path = _active_dictionary_path()
		if not path:
			return []
		try:
			stat = os.stat(path)
			signature = (path, stat.st_mtime_ns, stat.st_size)
		except Exception:
			signature = (path, None, None)
		if signature == getattr(self, "_dictionaryCacheSignature", None):
			return self._dictionaryCache
		compiled = []
		for entry in _load_native_exceptions():
			source = entry["source"]
			match_type = entry["matchType"]
			flags = 0 if entry["caseSensitive"] else re.IGNORECASE
			if match_type == EXCEPTION_MATCH_REGEX:
				pattern = source
			else:
				pattern = re.escape(source)
				if match_type == EXCEPTION_MATCH_WHOLE_WORD:
					pattern = r"(?<!\w)%s(?!\w)" % pattern
				elif match_type == EXCEPTION_MATCH_STARTS_WITH:
					pattern = r"(?<!\w)%s" % pattern
				elif match_type == EXCEPTION_MATCH_ENDS_WITH:
					pattern = r"%s(?!\w)" % pattern
			try:
				compiled.append((re.compile(pattern, flags), entry["replacement"]))
			except Exception:
				continue
		self._dictionaryCacheSignature = signature
		self._dictionaryCache = compiled
		return compiled

	def append(self, parameters, text):
		text = text.translate(self.table)
		param_str = b"".join(struct.pack('4I', *x) for x in parameters)
		text_bytes = text.encode('utf-16le')
		self._send_command("append", parameters_bytes=param_str, text_bytes=text_bytes)

	def speak_append(self):
		self._send_command("speakAppend")

	def _set_rate(self, rate):
		rate = self._clampPercent(rate)
		if rate <= 50:
			self._rate = self._clampParam(
				BUILD18_RATE_MIN + (BUILD18_RATE_DEFAULT - BUILD18_RATE_MIN) * (rate / 50.0),
				BUILD18_RATE_MIN,
				BUILD18_RATE_DEFAULT,
			)
		else:
			self._rate = self._clampParam(
				BUILD18_RATE_DEFAULT + (BUILD18_RATE_MAX - BUILD18_RATE_DEFAULT) * ((rate - 50) / 50.0),
				BUILD18_RATE_DEFAULT,
				BUILD18_RATE_MAX,
			)

	def _get_rate(self):
		rate = self._clampParam(getattr(self, "_rate", BUILD18_RATE_DEFAULT), BUILD18_RATE_MIN, BUILD18_RATE_MAX)
		if rate <= BUILD18_RATE_DEFAULT:
			return self._clampPercent(round(((rate - BUILD18_RATE_MIN) / float(BUILD18_RATE_DEFAULT - BUILD18_RATE_MIN)) * 50))
		return self._clampPercent(round(50 + ((rate - BUILD18_RATE_DEFAULT) / float(BUILD18_RATE_MAX - BUILD18_RATE_DEFAULT)) * 50))

	def _clampPercent(self, value):
		"""Clamp a value to NVDA's standard 0..100 percent range."""
		try:
			v = int(value)
		except Exception:
			v = 0
		return max(0, min(100, v))

	def _clampParam(self, value, minv, maxv):
		try:
			v = int(round(value))
		except Exception:
			v = minv
		return max(minv, min(maxv, v))

	def _pauseSettingToOrpheusMs(self, value, maxMs):
		value = self._clampPercent(value)
		return self._clampParam((value / 100.0) * maxMs, 0, maxMs)

	def _legacyPauseMsToSetting(self, value, maxMs):
		value = self._clampParam(value, 0, maxMs)
		return self._clampPercent(round((value / float(maxMs)) * 100))

	def _coercePauseSetting(self, value, maxMs):
		try:
			v = int(round(value))
		except Exception:
			return 0
		if v > 100:
			return self._legacyPauseMsToSetting(v, maxMs)
		return self._clampPercent(v)

	def _parameterSettingToOrpheusValue(self, key, value):
		if key == "wordPauseAmount":
			return self._pauseSettingToOrpheusMs(value, 1000)
		if key == "phrasePauseAmount":
			return self._pauseSettingToOrpheusMs(value, 2000)
		return value

	def _migrateLegacyPauseSettings(self):
		try:
			conf = config.conf["speech"][self.name]
		except Exception:
			return
		try:
			if conf.get("wordPauseAmount") is None and conf.get("wordPause") is not None:
				self.wordPauseAmount = self._legacyPauseMsToSetting(conf["wordPause"], 1000)
				conf["wordPauseAmount"] = self.wordPauseAmount
			if conf.get("phrasePauseAmount") is None and conf.get("phrasePause") is not None:
				self.phrasePauseAmount = self._legacyPauseMsToSetting(conf["phrasePause"], 2000)
				conf["phrasePauseAmount"] = self.phrasePauseAmount
		except Exception:
			pass

	def _migrateLegacyVoiceSetting(self):
		try:
			conf = config.conf["speech"][self.name]
		except Exception:
			return
		try:
			voice = conf.get("voice")
		except Exception:
			return
		if str(voice or "").startswith("lang"):
			return
		migrated = _countryFromVoiceId(voice)
		try:
			conf["voice"] = _voiceIdFromCountry(migrated)
		except Exception:
			pass

	def _get_default_pitch(self):
		desc = getattr(self, "_pitchDesc", None)
		if desc is not None and desc.current:
			return self._clampParam(desc.current, desc.min, desc.max)
		return self._clampParam(FALLBACK_DEFAULT_PITCH, self._pitchMin, self._pitchMax)

	def _pitchPercentToParam(self, pct):
		"""Convert an NVDA pitch percentage (0..100) to Orpheus pitch parameter."""
		pct = self._clampPercent(pct)
		minv = getattr(self, "_pitchMin", FALLBACK_PITCH_MIN)
		maxv = getattr(self, "_pitchMax", FALLBACK_PITCH_MAX)
		pivot = self._clampParam(getattr(self, "_defaultPitch", FALLBACK_DEFAULT_PITCH), minv, maxv)

		if pct <= 50:
			val = minv + (pivot - minv) * (pct / 50.0)
		else:
			val = pivot + (maxv - pivot) * ((pct - 50) / 50.0)
		return self._clampParam(val, minv, maxv)

	def _inlinePitchCommandToParam(self, item):
		if hasattr(item, "offset"):
			return self._clampParam(self._pitch + item.offset, self._pitchMin, self._pitchMax)
		multiplier = getattr(item, "multiplier", 1)
		return self._pitchPercentToParam(self._clampPercent(self._pitchPercent * multiplier))

	def _replaceParamAtOffset(self, parameters, offset, paramId, value):
		# Keep the last inline value if duplicate parameters share an offset.
		offset = max(0, offset)
		parameters[:] = [p for p in parameters if not (p[0] == offset and p[2] == paramId)]
		parameters.append((offset, 0, paramId, value))

	def _normaliseLang(self, lang):
		if not lang:
			return None
		return str(lang).replace("_", "-").lower()

	def _voiceForLang(self, lang):
		normalised = self._normaliseLang(lang)
		if not normalised:
			return self._voice
		langs = self._get_cached_langs()
		primary = normalised.split("-", 1)[0]
		fallbackVoice = None
		for index, langInfo in enumerate(langs):
			country = langInfo.get("country")
			code = self._normaliseLang(LANGUAGES.get(country, langInfo.get("lang")))
			if not code:
				continue
			if code == normalised:
				if country == getattr(self, "_voice", None):
					return self._voice
				return country
			if fallbackVoice is None and code.split("-", 1)[0] == primary:
				fallbackVoice = country
		if fallbackVoice is not None:
			return fallbackVoice
		return self._voice

	def _set_volume(self, volume):
		# Orpheus doesn't provide a stable API for per-utterance volume.
		# We implement NVDA's Volume setting by scaling the PCM samples.
		self._volume = self._clampPercent(volume)

	def _get_volume(self):
		return getattr(self, "_volume", 100)

	def _set_pitch(self, pitch):
		self._pitchPercent = self._clampPercent(pitch)
		self._pitch = self._pitchPercentToParam(self._pitchPercent)

	def _get_pitch(self):
		return getattr(self, "_pitchPercent", 50)

	def _get_intonation(self):
		return getattr(self, "_intonation", 50)

	def _set_intonation(self, value):
		self._intonation = self._clampPercent(value)

	def _get_voicing(self):
		return getattr(self, "_voicing", BUILD18_DEFAULT_VOICING)

	def _set_voicing(self, value):
		self._voicing = self._clampPercent(value)

	def _get_voiceSource(self):
		return getattr(self, "_voiceSource", BUILD18_DEFAULT_VOICE_SOURCE)

	def _set_voiceSource(self, value):
		self._voiceSource = max(0, min(1, int(value)))

	def _get_wordPauseAmount(self):
		return getattr(self, "_wordPauseAmount", 0)

	def _set_wordPauseAmount(self, value):
		self._wordPauseAmount = self._coercePauseSetting(value, 1000)

	def _get_phrasePauseAmount(self):
		return getattr(self, "_phrasePauseAmount", 12)

	def _set_phrasePauseAmount(self, value):
		self._phrasePauseAmount = self._coercePauseSetting(value, 2000)

	def cancel(self):
		with self.lock:
			was_speaking = self.is_speaking
			self.is_speaking = False
			self.queue = []
			if self._fallback_done_timer is not None:
				self._fallback_done_timer.cancel()
				self._fallback_done_timer = None
			if self._estimated_done_timer is not None:
				self._estimated_done_timer.cancel()
				self._estimated_done_timer = None
			self._cancel_estimated_index_timers()
			self._speech_run_id += 1
			if self._terminating:
				self._mute_pending = False
			elif was_speaking:
				self._mute_pending = True
		if self.player is not None:
			self.player.stop()
		self._work_event.set()

	def _refresh_language_cache(self):
		try:
			langs = self._send_command("getLangs", timeout=METADATA_COMMAND_TIMEOUT)
		except Exception:
			return
		if langs:
			self._langs = langs

	def _get_cached_langs(self):
		return self._langs

	def _get_availableVoices(self):
		langs = self._get_cached_langs()
		infos = {}
		for l in langs:
			country_code = l['country']
			name = l.get("name") or LANGUAGES.get(country_code, l.get("lang") or str(country_code))
			voice_id = _voiceIdFromCountry(country_code)
			infos[voice_id] = VoiceInfo(voice_id, name, LANGUAGES.get(country_code, str(country_code)))
		if not infos:
			voice_id = _voiceIdFromCountry(getattr(self, "_voice", 44))
			infos[voice_id] = VoiceInfo(voice_id, "Orpheus", DEFAULT_LANGUAGE)
		return infos

	def _get_voice(self):
		return _voiceIdFromCountry(getattr(self, "_voice", 44))

	def _set_voice(self, voice):
		self._voice = _countryFromVoiceId(voice)
		self._refresh_language_cache()
		if getattr(self, "_loadingSettings", False):
			return
		self._apply_current_voice()
		self._refresh_current_voice_params()

	def _apply_current_voice(self):
		if getattr(self, "_process", None) is None:
			return
		try:
			self.speak([' '])
		except Exception:
			pass

	def _refresh_current_voice_params(self):
		# Refresh parameter descriptors and update cached pitch bounds. After a
		# voice change, Orpheus reports that voice's default pitch as current.
		self._paramDescs = self.get_params() or []
		self._pitchDesc = self._paramDescs[1] if len(self._paramDescs) > 1 else None
		self._pitchMin = self._pitchDesc.min if self._pitchDesc else FALLBACK_PITCH_MIN
		self._pitchMax = self._pitchDesc.max if self._pitchDesc else FALLBACK_PITCH_MAX
		self._defaultPitch = self._get_default_pitch()
		self._refresh_parameter_ids()
		self._pitch = self._pitchPercentToParam(getattr(self, "_pitchPercent", 50))

	def _refresh_parameter_ids(self):
		self._parameterIds = {}
		for key, tokens, _label, _min_value, _max_value, _default, _min_step, _normal_step, _large_step, _display_name, fallback_id in PARAMETER_SETTINGS:
			match = None
			for index, desc in enumerate(getattr(self, "_paramDescs", ()) or ()):
				name = str(getattr(desc, "name", "") or "").lower()
				if all(token in name for token in tokens):
					match = index
					break
			self._parameterIds[key] = match if match is not None else fallback_id

	def _parameterIdFor(self, key, fallback_id):
		try:
			return int(getattr(self, "_parameterIds", {}).get(key, fallback_id))
		except Exception:
			return fallback_id

	def terminate(self):
		self._terminating = True
		try:
			self.cancel()
		except Exception:
			pass
		try:
			self._send_command("close", timeout=1.0)
		except Exception:
			pass
		self._stop_reader = True
		self._work_event.set()
		self._stop_host_process()
		try:
			self._conn.close()
		except Exception:
			pass
		if self.player is not None:
			self.player.close()
			self.player = None

	def _restart_stalled_host(self):
		old_connection = getattr(self, "_conn", None)
		if old_connection is not None:
			try:
				old_connection.shutdown(socket.SHUT_RDWR)
			except Exception:
				pass
			try:
				old_connection.close()
			except Exception:
				pass
		process = getattr(self, "_process", None)
		if process is not None and process.poll() is None:
			try:
				process.terminate()
				process.wait(timeout=0.5)
			except subprocess.TimeoutExpired:
				try:
					process.kill()
					process.wait(timeout=0.5)
				except Exception:
					pass
			except Exception:
				pass
		self._process = None
		reader = getattr(self, "_reader_thread", None)
		if reader is not None and reader is not threading.current_thread():
			reader.join(timeout=1.0)
		with self._msg_lock:
			for _msg_id, (evt, _) in self._response_events.items():
				evt.set()
			self._response_events = {}
		self._start_host()

	def _stop_host_process(self):
		process = getattr(self, "_process", None)
		if process is None:
			return
		if process.poll() is not None:
			self._process = None
			return
		try:
			process.wait(timeout=2.0)
			self._process = None
			return
		except subprocess.TimeoutExpired:
			pass
		try:
			process.terminate()
			process.wait(timeout=2.0)
			self._process = None
			return
		except subprocess.TimeoutExpired:
			pass
		except Exception:
			self._process = None
			return
		try:
			process.kill()
			process.wait(timeout=2.0)
		except Exception:
			pass
		finally:
			self._process = None

	def get_params(self):
		try:
			data = self._send_command("getParams", timeout=METADATA_COMMAND_TIMEOUT)
			return [ParamDesc(**d) for d in data]
		except:
			return []

	def pause(self, switch):
		if self.player is not None:
			self.player.pause(switch)

