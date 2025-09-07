# I do not know python.  This is 100% vibeslop piled on top of somebody else's work.  

from PIL import Image
from PIL.PngImagePlugin import PngImageFile, PngInfo
from functools import partial
import base64
import json
import os
import shutil
import sys
import traceback
import re

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QListWidget, QLabel, QListWidgetItem, QStackedWidget, QSplitter,
    QLineEdit, QPlainTextEdit, QPushButton, QFormLayout, QTabWidget, QHBoxLayout, QFileDialog,
    QCheckBox, QSizePolicy, QComboBox, QGridLayout, QAbstractItemView, QMessageBox, QInputDialog,
    QGroupBox, QSpinBox, QDoubleSpinBox
)
from PyQt5.QtGui import (
    QIntValidator, QDoubleValidator, QPixmap, QPainter, QTextCursor,
    QSyntaxHighlighter, QTextCharFormat, QIcon
)
from PyQt5.QtCore import Qt, QSize, pyqtSignal

try:
    import enchant
except Exception:
    enchant = None

# Network dependency for AI calls
try:
    import requests
except Exception:
    requests = None

DEFAULT_SPELL_LANG = "en_US"

# -------------------------------
# Defaults for AI tab
# -------------------------------

DEFAULT_OPENAI_BASE = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_KCPP_BASE = "http://localhost:5001"  # typical local KoboldCPP

CHATGPT_DEFAULT_PROMPT = (
    "You generate TavernAI 'chara_card_v2' content from a short brief.\n"
    "Return ONLY a single JSON object, no markdown, no commentary.\n"
    "Schema: {\n"
    "  \"description\": string,\n"
    "  \"personality\": string,\n"
    "  \"scenario\": string,\n"
    "  \"first_mes\": string,\n"
    "  \"mes_example\": string\n"
    "}\n\n"
    "General rules: write vivid but concise prose. Keep each field under ~1,200 words.\n"
    "Use immersive sensory detail and natural dialogue. Do not write any user lines inside 'first_mes'.\n"
    "Format 'mes_example' as one or more blocks that begin with <EXAMPLE START> and end with <EXAMPLE END>.\n"
    "Inside examples, prefix assistant lines with {{char}}: and keep {{user}} lines minimal.\n\n"
    "Two modes based on the brief:\n"
    "1) Single Character mode: If the brief describes a person, fill fields for an individual.\n"
    "   - 'description': appearance, background, skills, notable props, setting.\n"
    "   - 'personality': layered traits, quirks, strengths, vulnerabilities.\n"
    "   - 'scenario': a present-moment scene that makes sense for their life.\n"
    "   - 'first_mes': an in-character opening message the bot would send first.\n"
    "   - 'mes_example': 1–2 short exchanges that demonstrate voice and style.\n\n"
    "2) Open-World / Scenario mode: If the brief asks for a sandbox, narrator, or world, write world rules.\n"
    "   - 'description': how {{char}} functions in the world, constraints, content rules, tone.\n"
    "   - 'personality': the world's logic, neighborhoods/regions or factions, how NPCs behave, limits.\n"
    "   - 'scenario': the entry scene that onboards {{user}} with goals and choices.\n"
    "   - 'first_mes': the opening narration or guide message.\n"
    "   - 'mes_example': 1–2 brief example interactions showing typical play.\n\n"
    "Quality requirements: coherent POV, no contradictions with earlier fields, respect platform safety.\n"
    "Start your output directly with '{' and end with '}'."
)

# -------------------------------
# Card data scaffolding and I/O
# -------------------------------

base = {
    'spec': 'chara_card_v2',
    'spec_version': '2.0',
    'data': {
        'name': '',
        'description': "",
        'personality': '',
        'scenario': "",
        'first_mes': '',
        'mes_example': '',
        'creator_notes': '',
        'system_prompt': '',
        'post_history_instructions': '',
        'alternate_greetings': [],
        'tags': [],
        'creator': '',
        'character_version': '',
        'extensions': {}
    }
}

PLAINTEXT_EDITOR_MAX_HEIGHT = 50
DIRTY_CHARACTER_COLOUR = "background-color: #FFFF00;"


def excepthook(exc_type, exc_value, exc_tb):
    tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    print("caught:", tb)
    sys.__excepthook__(exc_type, exc_value, exc_tb)


sys.excepthook = excepthook


def deep_empty_card():
    return json.loads(json.dumps(base))  # deep copy


# Extract JSON character data from an image. Handles both V1 and V2 TavernAI format, returns V2.
# Creates a new character data dict if the image doesn't have one.
def read_character(path):
    image = PngImageFile(path)
    user_comment = image.text.get('chara', None)
    if user_comment is None:
        return deep_empty_card()
    base64_bytes = user_comment.encode('utf-8')
    json_bytes = base64.b64decode(base64_bytes)
    json_str = json_bytes.decode('utf-8')
    data = json.loads(json_str)

    if data.get('spec') != 'chara_card_v2':
        newData = deep_empty_card()
        newData["data"] = data
        data = newData
    if not isinstance(data["data"].get("tags", []), list):
        data["data"]["tags"] = []
    if not isinstance(data["data"].get("alternate_greetings", []), list):
        data["data"]["alternate_greetings"] = []
    if "character_book" in data["data"] and "entries" in data["data"]["character_book"]:
        for entry in data["data"]["character_book"]["entries"]:
            if not isinstance(entry.get("secondary_keys"), list):
                entry["secondary_keys"] = []
    return data


# Writes character data back to the image
def write_character(path, data):
    json_str = json.dumps(data)
    base64_str = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')
    image = Image.open(path)
    metadata = PngInfo()
    metadata.add_text('chara', base64_str)
    image.save(path, 'PNG', pnginfo=metadata)


# -------------- helpers --------------

def available_spell_langs():
    if enchant is None:
        return []
    try:
        langs = sorted({tag for (tag, _prov) in enchant.list_dicts()})
        if DEFAULT_SPELL_LANG in langs:
            langs.remove(DEFAULT_SPELL_LANG)
            langs.insert(0, DEFAULT_SPELL_LANG)
        return langs
    except Exception:
        return [DEFAULT_SPELL_LANG]


def convertBoolToTristate(data):
    if data is True:
        return Qt.Checked
    elif data is False:
        return Qt.Unchecked
    return Qt.PartiallyChecked


def convertTristateToBool(data):
    if data == Qt.Checked:
        return True
    elif data == Qt.Unchecked:
        return False
    return None


def safeJSONLoads(jsonstring):
    try:
        return json.loads(jsonstring)
    except Exception:
        return jsonstring


def safeNumberConversion(stringVal, default=None):
    try:
        return float(stringVal)
    except ValueError:
        return default


def updateOrDeleteKey(dictionary, key, value, nullvalue=None):
    if value != nullvalue:
        dictionary[key] = value
    elif key in dictionary:
        del dictionary[key]


# --------- Character book transform helpers ----------

def process_worldbook(data):
    if not isinstance(data, dict):
        return None
    if "entries" not in data:
        if "spec" in data and data["spec"] == 'chara_card_v2' and "data" in data and "character_book" in data["data"]:
            return data["data"]["character_book"]
        return None
    if isinstance(data["entries"], dict):
        entries = list(data["entries"].values())
        data["entries"] = entries
    for entry in data["entries"]:
        if "entry" in entry and entry.get("content") == entry.get("entry"):
            del entry["entry"]
    return data


def import_worldbook(characterBook, worldBook):
    desc = worldBook.get("description", "")
    if desc != "" and characterBook.get("description", "") == "":
        characterBook["description"] = desc
    name = worldBook.get("name", "")
    if name != "" and characterBook.get("name", "") == "":
        characterBook["name"] = name
    characterBook["entries"] = characterBook.get("entries", [])
    characterBook["entries"] += worldBook["entries"]
    worldExtensions = worldBook.get("extensions", {})
    characterExtensions = characterBook.get("extensions", {})
    characterBook["extensions"] = characterExtensions | worldExtensions
    return characterBook


# ----------------- Spellcheck -----------------

class SpellCheckHighlighter(QSyntaxHighlighter):
    """Underline misspelled words with a red wavy line in a QPlainTextEdit."""
    WORD_RE = re.compile(r"[A-Za-z']+")

    def __init__(self, document, lang="en_US"):
        super().__init__(document)
        self.dict = enchant.Dict(lang) if enchant else None
        self.format = QTextCharFormat()
        self.format.setUnderlineColor(Qt.red)
        self.format.setUnderlineStyle(QTextCharFormat.WaveUnderline)

    def highlightBlock(self, text):
        if not self.dict:
            return
        for m in self.WORD_RE.finditer(text):
            word = m.group(0)
            if len(word) < 2 or word.isupper():
                continue
            if not self.dict.check(word):
                self.setFormat(m.start(), m.end() - m.start(), self.format)
    
    def setLanguage(self, lang):
        self.dict = enchant.Dict(lang) if enchant else None
        self.rehighlight()


class SpellCheckPlainTextEdit(QPlainTextEdit):
    """QPlainTextEdit that highlights misspellings and offers right-click suggestions."""
    def __init__(self, parent=None, lang="en_US"):
        super().__init__(parent)
        self._dict = enchant.Dict(lang) if enchant else None
        self._hl = SpellCheckHighlighter(self.document(), lang) if enchant else None

    def _word_under_cursor(self, pos):
        cursor = self.cursorForPosition(pos)
        cursor.select(QTextCursor.WordUnderCursor)
        word = cursor.selectedText()
        return cursor, word

    def _replace_with(self, cursor, replacement, _checked=False):
        c = QTextCursor(cursor)
        c.beginEditBlock()
        c.insertText(replacement)
        c.endEditBlock()

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu()
        if not self._dict:
            menu.exec_(event.globalPos())
            return
        cursor, word = self._word_under_cursor(event.pos())
        if word and re.fullmatch(r"[A-Za-z']+", word) and not self._dict.check(word):
            actions = menu.actions()
            if actions:
                menu.insertSeparator(actions[0])
            suggestions = self._dict.suggest(word)[:6]
            if suggestions:
                for s in suggestions:
                    act = menu.addAction(f"Replace with “{s}”")
                    act.triggered.connect(partial(self._replace_with, cursor, s))
            else:
                noact = menu.addAction("No suggestions")
                noact.setEnabled(False)
        menu.exec_(event.globalPos())
    
    def setLanguage(self, lang):
        if enchant is None:
            return
        self._dict = enchant.Dict(lang)
        if self._hl:
            self._hl.setLanguage(lang)


# ----------------- UI Widgets -----------------

class AlternateGreetingWidget(QWidget):
    def __init__(self, parent):
        super(AlternateGreetingWidget, self).__init__(parent)
        self.parentEditor = parent

        self.layout = QHBoxLayout(self)
        self.setLayout(self.layout)
        self.editor = SpellCheckPlainTextEdit(self)
        self.editor.textChanged.connect(self.setDirty)
        self.delete_button = QPushButton("Delete", self)
        self.layout.addWidget(self.editor)
        self.layout.addWidget(self.delete_button)

    def setDirty(self):
        self.parentEditor.setDirty()


class EntryWidget(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.characterBookParent = parent

        self.layout = QVBoxLayout(self)
        self.setLayout(self.layout)

        self.simple_attributes = QWidget(self)
        self.simple_attributes_layout = QGridLayout(self.simple_attributes)
        self.layout.addWidget(self.simple_attributes)

        self.simple_attributes_layout.addWidget(QLabel("Keys", self.simple_attributes), 0, 0)
        self.keys_field = QLineEdit(self.simple_attributes)
        self.keys_field.textChanged.connect(self.setDirty)
        self.simple_attributes_layout.addWidget(self.keys_field, 0, 1)
        self.delete_button = QPushButton("Delete", self)
        self.simple_attributes_layout.addWidget(self.delete_button, 0, 2)
        self.simple_attributes_layout.addWidget(QLabel("Content", self.simple_attributes), 1, 0)
        self.content_field = SpellCheckPlainTextEdit(self.simple_attributes)
        self.content_field.textChanged.connect(self.setDirty)
        self.content_field.setMaximumHeight(PLAINTEXT_EDITOR_MAX_HEIGHT)
        self.content_field.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.simple_attributes_layout.addWidget(self.content_field, 1, 1, 1, 2)

        self.complex_attributes = QWidget(self)
        self.complex_attributes_layout = QGridLayout(self.complex_attributes)
        self.layout.addWidget(self.complex_attributes)
        grid = self.complex_attributes_layout

        grid.addWidget(QLabel("Name", self), 0, 0)
        self.name_edit = QLineEdit(self)
        self.name_edit.textChanged.connect(self.setDirty)
        self.name_edit.setToolTip("not used in prompt engineering")
        grid.addWidget(self.name_edit, 0, 1)

        self.copyKeysButton = QPushButton("Copy Keys", self)
        self.copyKeysButton.setToolTip('Copy the "Keys" field into the "Name" field')
        self.copyKeysButton.clicked.connect(self.copy_keys)
        grid.addWidget(self.copyKeysButton, 0, 2)

        self.booleans = QWidget(self)
        self.booleans_layout = QHBoxLayout(self.booleans)
        self.booleans.setLayout(self.booleans_layout)
        grid.addWidget(self.booleans, 1, 0, 1, 3)
        bools = self.booleans_layout
        self.enabled_checkbox = QCheckBox("Enabled", self)
        self.enabled_checkbox.setToolTip("Whether this entry is to be actually used by the character.")
        self.enabled_checkbox.stateChanged.connect(self.updateWidgetEnabled)
        bools.addWidget(self.enabled_checkbox)
        self.case_sensitive_checkbox = QCheckBox("Case Sensitive", self)
        self.case_sensitive_checkbox.setTristate(True)
        self.case_sensitive_checkbox.setToolTip(
            "Tristate: true, false, or undefined. Undefined removes the key altogether."
        )
        self.case_sensitive_checkbox.stateChanged.connect(self.setDirty)
        bools.addWidget(self.case_sensitive_checkbox)
        self.constant_checkbox = QCheckBox("Constant", self)
        self.constant_checkbox.setTristate(True)
        self.constant_checkbox.setToolTip("Tristate: true, false, or undefined.")
        self.constant_checkbox.stateChanged.connect(self.setDirty)
        bools.addWidget(self.constant_checkbox)
        positionLabel = QLabel("Position")
        positionLabel.setAlignment(Qt.AlignRight)
        bools.addWidget(positionLabel)
        self.positionBox = QComboBox(self)
        self.positionBox.addItem("")
        self.positionBox.addItem("Before character")
        self.positionBox.addItem("After character")
        self.positionBox.setToolTip("whether the entry is placed before or after the character defs")
        self.positionBox.currentIndexChanged.connect(self.setDirty)
        bools.addWidget(self.positionBox)

        doubleValidator = QDoubleValidator()
        self.numbers = QWidget(self)
        self.numbers_layout = QHBoxLayout(self.numbers)
        self.numbers.setLayout(self.numbers_layout)
        grid.addWidget(self.numbers, 2, 0, 1, 3)
        nums = self.numbers_layout
        nums.addWidget(QLabel("Insertion Order", self))
        self.insertion_order_edit = QLineEdit(self)
        self.insertion_order_edit.setToolTip("if two entries inserted, a lower insertion order causes it to be inserted higher")
        self.insertion_order_edit.setValidator(doubleValidator)
        self.insertion_order_edit.textChanged.connect(self.setDirty)
        nums.addWidget(self.insertion_order_edit)
        nums.addWidget(QLabel("Priority", self))
        self.priority_edit = QLineEdit(self)
        self.priority_edit.setToolTip("if token budget reached, lower priority value entries are discarded first")
        self.priority_edit.setValidator(doubleValidator)
        self.priority_edit.textChanged.connect(self.setDirty)
        nums.addWidget(self.priority_edit)
        nums.addWidget(QLabel("ID", self))
        self.id_edit = QLineEdit(self)
        self.id_edit.setToolTip("not used in prompt engineering")
        self.id_edit.setValidator(doubleValidator)
        self.id_edit.textChanged.connect(self.setDirty)
        nums.addWidget(self.id_edit)

        grid.addWidget(QLabel("Comment", self), 3, 0)
        self.comment_edit = SpellCheckPlainTextEdit(self)
        self.comment_edit.setMaximumHeight(PLAINTEXT_EDITOR_MAX_HEIGHT)
        self.comment_edit.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.comment_edit.setToolTip("not used in prompt engineering")
        self.comment_edit.textChanged.connect(self.setDirty)
        grid.addWidget(self.comment_edit, 3, 1, 1, 2)
        self.selective_checkbox = QCheckBox("Selective", self)
        self.selective_checkbox.setTristate(True)
        self.selective_checkbox.stateChanged.connect(self.setSelective)
        self.selective_checkbox.setToolTip(
            "If true, require a key from both 'keys' and 'secondary_keys' to trigger the entry."
        )
        grid.addWidget(self.selective_checkbox, 4, 0)
        self.secondary_keys_edit = QLineEdit(self)
        self.secondary_keys_edit.setToolTip("comma-separated secondary keys, only used if selective is true.")
        self.secondary_keys_edit.textChanged.connect(self.setDirty)
        grid.addWidget(self.secondary_keys_edit, 4, 1, 1, 2)
        grid.addWidget(QLabel("Extensions", self), 5, 0)
        self.extensions_edit = QPlainTextEdit(self)
        self.extensions_edit.setMaximumHeight(PLAINTEXT_EDITOR_MAX_HEIGHT)
        self.extensions_edit.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.extensions_edit.setToolTip("A block of JSON values used by non-standard chatbot extensions.")
        self.extensions_edit.textChanged.connect(self.setDirty)
        grid.addWidget(self.extensions_edit, 5, 1, 1, 2)

    def setSelective(self, state):
        self.secondary_keys_edit.setEnabled(state == Qt.Checked)
        self.setDirty()

    def copy_keys(self):
        self.name_edit.setText(self.keys_field.text())
        self.setDirty()

    def updateWidgetEnabled(self):
        if self.enabled_checkbox.checkState() != Qt.Checked:
            self.setStyleSheet("background-color: #D3D3D3;")
        else:
            self.setStyleSheet("")
        self.setDirty()

    def setDirty(self):
        self.characterBookParent.setDirty()

    def setData(self, entry):
        if not entry:
            self.enabled_checkbox.setChecked(True)
            self.extensions_edit.setPlainText("{}")
            return
        self.content_field.setPlainText(entry.get("content"))
        self.keys_field.setText(", ".join(entry.get("keys", [])))
        self.name_edit.setText(entry.get("name"))
        self.enabled_checkbox.setChecked(entry.get("enabled", True))
        self.updateWidgetEnabled()
        self.case_sensitive_checkbox.setCheckState(convertBoolToTristate(entry.get("case_sensitive", None)))
        self.constant_checkbox.setCheckState(convertBoolToTristate(entry.get("constant", None)))
        position = entry.get("position", "")
        if position == "before_char":
            position = "Before character"
        elif position == "after_char":
            position = "After character"
        else:
            position = ""
        self.positionBox.setCurrentText(position)
        self.insertion_order_edit.setText(str(entry.get("insertion_order", "0")))
        self.priority_edit.setText(str(entry.get("priority", "")))
        self.id_edit.setText(str(entry.get("id", "")))
        self.comment_edit.setPlainText(entry.get("comment"))
        self.selective_checkbox.setCheckState(convertBoolToTristate(entry.get("selective", None)))
        self.secondary_keys_edit.setText(", ".join(entry.get("secondary_keys", [])))
        self.secondary_keys_edit.setEnabled(entry.get("selective", False))
        self.extensions_edit.setPlainText(json.dumps(entry.get("extensions", {})))

    def getData(self):
        entry_dict = {}
        entry_dict["keys"] = [x.strip() for x in str(self.keys_field.text()).split(',')]
        entry_dict["content"] = self.content_field.toPlainText()
        entry_dict["extensions"] = safeJSONLoads(self.extensions_edit.toPlainText())
        entry_dict["enabled"] = self.enabled_checkbox.checkState() == Qt.Checked
        entry_dict["insertion_order"] = safeNumberConversion(self.insertion_order_edit.text(), 0)
        updateOrDeleteKey(entry_dict, "case_sensitive", convertTristateToBool(self.case_sensitive_checkbox.checkState()))
        updateOrDeleteKey(entry_dict, "name", self.name_edit.text(), "")
        updateOrDeleteKey(entry_dict, "priority", safeNumberConversion(self.priority_edit.text()))
        updateOrDeleteKey(entry_dict, "id", safeNumberConversion(self.id_edit.text()))
        updateOrDeleteKey(entry_dict, "comment", self.comment_edit.toPlainText(), "")
        updateOrDeleteKey(entry_dict, "selective", convertTristateToBool(self.selective_checkbox.checkState()))
        updateOrDeleteKey(entry_dict, "secondary_keys", [x.strip() for x in str(self.secondary_keys_edit.text()).split(',')])
        updateOrDeleteKey(entry_dict, "constant", convertTristateToBool(self.constant_checkbox.checkState()))
        position = self.positionBox.currentText()
        if position == "Before character":
            entry_dict["position"] = "before_char"
        elif position == "After character":
            entry_dict["position"] = "after_char"
        return entry_dict


class CharacterBookWidget(QWidget):
    def __init__(self, fullData, parent):
        super().__init__(parent)
        self.editorParent = parent
        self.fullData = fullData
        self.layout = QVBoxLayout(self)

        self.view_checkbox = QCheckBox("Simple View", self)
        self.view_checkbox.stateChanged.connect(self.toggle_view)
        self.layout.addWidget(self.view_checkbox)

        self.simple_attributes = QWidget(self)
        self.simple_attributes_layout = QFormLayout(self.simple_attributes)
        self.layout.addWidget(self.simple_attributes)

        self.name_field = QLineEdit(self)
        self.name_field.textChanged.connect(self.setDirty)
        self.simple_attributes_layout.addRow("Name", self.name_field)
        self.description_field = SpellCheckPlainTextEdit(self)
        self.description_field.setMaximumHeight(PLAINTEXT_EDITOR_MAX_HEIGHT)
        self.description_field.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.description_field.textChanged.connect(self.setDirty)
        self.simple_attributes_layout.addRow("Description", self.description_field)

        self.complex_attributes = QWidget(self)
        self.complex_attributes_layout = QHBoxLayout(self.complex_attributes)
        self.layout.addWidget(self.complex_attributes)

        intValidator = QIntValidator()

        self.scan_depth_label = QLabel("Scan Depth", self)
        self.complex_attributes_layout.addWidget(self.scan_depth_label)
        self.scan_depth_editor = QLineEdit("", self)
        self.scan_depth_editor.setToolTip("Chat history depth scanned for keywords.")
        self.scan_depth_editor.setValidator(intValidator)
        self.scan_depth_editor.textChanged.connect(self.setDirty)
        self.complex_attributes_layout.addWidget(self.scan_depth_editor)
        self.token_budget_label = QLabel("Token Budget", self)
        self.complex_attributes_layout.addWidget(self.token_budget_label)
        self.token_budget_editor = QLineEdit("", self)
        self.token_budget_editor.setToolTip("Sets how much of the context can be taken up by entries.")
        self.token_budget_editor.setValidator(intValidator)
        self.token_budget_editor.textChanged.connect(self.setDirty)
        self.complex_attributes_layout.addWidget(self.token_budget_editor)
        self.recursive_scanning = QCheckBox("Recursive Scanning", self)
        self.recursive_scanning.setToolTip("Tristate: true, false, or undefined.")
        self.recursive_scanning.setTristate(True)
        self.recursive_scanning.stateChanged.connect(self.setDirty)
        self.complex_attributes_layout.addWidget(self.recursive_scanning)

        self.extensions_form = QWidget(self)
        self.extensions_form_layout = QFormLayout(self.extensions_form)
        self.extensions_edit = QPlainTextEdit(self)
        self.extensions_edit.setMaximumHeight(PLAINTEXT_EDITOR_MAX_HEIGHT)
        self.extensions_edit.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.extensions_edit.setToolTip("A block of JSON values used by non-standard chatbot extensions.")
        self.extensions_edit.textChanged.connect(self.setDirty)
        self.extensions_form_layout.addRow("Extensions", self.extensions_edit)
        self.layout.addWidget(self.extensions_form)

        self.entries_list = QListWidget(self)
        self.entries_list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.entries_list.setStyleSheet("QListWidget::item { border-bottom: 1px solid black; }")
        self.layout.addWidget(self.entries_list)

        self.buttonWidget = QWidget(self)
        self.buttonWidgetLayout = QHBoxLayout()
        self.buttonWidget.setLayout(self.buttonWidgetLayout)
        self.layout.addWidget(self.buttonWidget)

        self.add_button = QPushButton("Add Entry", self)
        self.add_button.setToolTip("Insert a new blank entry at the bottom")
        self.add_button.clicked.connect(self.add_entry)
        self.buttonWidgetLayout.addWidget(self.add_button)

        self.importWorldbookButton = QPushButton("Import Worldbook", self)
        self.importWorldbookButton.setToolTip("Import entries from a worldbook or another character")
        self.importWorldbookButton.clicked.connect(self.import_worldbook)
        self.buttonWidgetLayout.addWidget(self.importWorldbookButton)

        self.view_checkbox.setChecked(True)

    def add_entry(self, entry=None):
        widget_item = QListWidgetItem(self.entries_list)
        custom_widget = EntryWidget(self)
        custom_widget.setData(entry)
        custom_widget.complex_attributes.setVisible(not self.view_checkbox.isChecked())
        widget_item.setSizeHint(custom_widget.sizeHint())
        self.entries_list.addItem(widget_item)
        self.entries_list.setItemWidget(widget_item, custom_widget)
        custom_widget.delete_button.clicked.connect(lambda: self.delete_entry(widget_item))
        self.setDirty()

    def import_worldbook(self):
        options = QFileDialog.Options()
        options |= QFileDialog.ReadOnly
        filepath = self.window().global_filepath
        fileName, _ = QFileDialog.getOpenFileName(self, "Import Worldbook", filepath, "JSON Files (*.json)", options=options)
        if fileName:
            with open(fileName, "r", encoding="utf-8") as f:
                worldBook = json.load(f)
                worldBook = process_worldbook(worldBook)
                if worldBook is None:
                    return
                characterBook = self.fullData["data"].get("character_book", {})
                self.fullData["data"]["character_book"] = characterBook
                import_worldbook(characterBook, worldBook)
                self.updateUIFromData()
                self.setDirty()

    def delete_entry(self, item):
        row = self.entries_list.row(item)
        self.entries_list.takeItem(row)
        self.setDirty()

    def toggle_view(self, state):
        self.complex_attributes.setVisible(state == Qt.Unchecked)
        self.extensions_form.setVisible(state == Qt.Unchecked)
        for i in range(self.entries_list.count()):
            item = self.entries_list.item(i)
            widget = self.entries_list.itemWidget(item)
            widget.complex_attributes.setVisible(state == Qt.Unchecked)
            sizeHint = widget.sizeHint()
            item.setSizeHint(sizeHint)
        self.entries_list.updateGeometry()

    def setDirty(self):
        self.editorParent.setDirty()

    def updateUIFromData(self):
        characterBook = self.fullData["data"].get("character_book", {})
        self.name_field.setText(characterBook.get("name", ""))
        self.description_field.setPlainText(characterBook.get("description", ""))
        self.scan_depth_editor.setText(str(characterBook.get("scan_depth", "")))
        self.token_budget_editor.setText(str(characterBook.get("token_budget", "")))
        self.recursive_scanning.setCheckState(convertBoolToTristate(characterBook.get("recursive_scanning", None)))
        self.extensions_edit.setPlainText(json.dumps(characterBook.get("extensions", {})))

        self.entries_list.clear()
        for entry in characterBook.get("entries", []):
            self.add_entry(entry)

    def updateDataFromUI(self):
        characterBook = self.fullData["data"].get("character_book", {})
        self.fullData["data"]["character_book"] = characterBook

        updateOrDeleteKey(characterBook, "name", self.name_field.text(), "")
        updateOrDeleteKey(characterBook, "description", self.description_field.toPlainText(), "")
        if self.scan_depth_editor.text() != "":
            characterBook["scan_depth"] = int(self.scan_depth_editor.text())
        elif "scan_depth" in characterBook:
            del characterBook["scan_depth"]
        if self.token_budget_editor.text() != "":
            characterBook["token_budget"] = int(self.token_budget_editor.text())
        elif "token_budget" in characterBook:
            del characterBook["token_budget"]
        updateOrDeleteKey(characterBook, "recursive_scanning", convertTristateToBool(self.recursive_scanning.checkState()))
        characterBook["extensions"] = safeJSONLoads(self.extensions_edit.toPlainText())

        entries = []
        for i in range(self.entries_list.count()):
            item = self.entries_list.item(i)
            entry = self.entries_list.itemWidget(item)
            entries.append(entry.getData())
        characterBook["entries"] = entries


# ----------------- AI-Assisted Generation -----------------

class AIAssistWidget(QWidget):
    
    # KoboldCPP chunked-generation defaults
    KCPP_DEFAULT_CHUNK = 400          # tokens per batch
    KCPP_DEFAULT_MAX_PASSES = 8       # how many chunks to try
    KCPP_DEFAULT_TIMEOUT = 60         # seconds per batch
    KCPP_BACK_CONTEXT_CHARS = 2000    # how many trailing chars of prior output to include when continuing

    def _kcpp_abort(self):
        """Ask KoboldCPP to abort any in-progress generation (best-effort)."""
        try:
            base = self.baseUrlEdit.text().rstrip('/')
            abort_url = base + "/api/extra/abort"
            requests.post(abort_url, timeout=2)
        except Exception:
            pass  # non-fatal


    def _stop_generation(self):
        """User clicked Stop."""
        self._stop_requested = True
        self._kcpp_abort()

    
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor  # EditorWidget
        self._stop_requested = False
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)

        # Backend settings
        backendBox = QGroupBox("Backend")
        backForm = QGridLayout(backendBox)
        root.addWidget(backendBox)

        self.backendCombo = QComboBox(self)
        self.backendCombo.addItems(["OpenAI Chat Completions", "KoboldCPP (\u2192 /api/v1/generate)"])
        self.backendCombo.currentIndexChanged.connect(self._on_backend_change)

        self.baseUrlEdit = QLineEdit(DEFAULT_OPENAI_BASE, self)
        self.modelEdit = QLineEdit(DEFAULT_OPENAI_MODEL, self)
        self.apiKeyEdit = QLineEdit("", self)
        self.apiKeyEdit.setEchoMode(QLineEdit.Password)
        self.tempSpin = QDoubleSpinBox(self)
        self.tempSpin.setRange(0.0, 2.0)
        self.tempSpin.setSingleStep(0.1)
        self.tempSpin.setValue(0.7)
        self.maxTokSpin = QSpinBox(self)
        self.maxTokSpin.setRange(64, 8192)
        self.maxTokSpin.setValue(1200)

        backForm.addWidget(QLabel("Provider"), 0, 0); backForm.addWidget(self.backendCombo, 0, 1)
        backForm.addWidget(QLabel("API Base URL"), 1, 0); backForm.addWidget(self.baseUrlEdit, 1, 1)
        backForm.addWidget(QLabel("Model"), 2, 0); backForm.addWidget(self.modelEdit, 2, 1)
        backForm.addWidget(QLabel("API Key"), 3, 0); backForm.addWidget(self.apiKeyEdit, 3, 1)
        backForm.addWidget(QLabel("Temperature"), 4, 0); backForm.addWidget(self.tempSpin, 4, 1)
        backForm.addWidget(QLabel("Max Tokens"), 5, 0); backForm.addWidget(self.maxTokSpin, 5, 1)

        # KoboldCPP chunk controls
        self.kcppBox = QGroupBox("KoboldCPP Chunked Generation")
        kcppForm = QGridLayout(self.kcppBox)
        root.addWidget(self.kcppBox)

        self.kcppChunkSpin = QSpinBox(self); self.kcppChunkSpin.setRange(64, 4096); self.kcppChunkSpin.setValue(self.KCPP_DEFAULT_CHUNK)
        self.kcppMaxPassesSpin = QSpinBox(self); self.kcppMaxPassesSpin.setRange(1, 50); self.kcppMaxPassesSpin.setValue(self.KCPP_DEFAULT_MAX_PASSES)
        self.kcppTimeoutSpin = QSpinBox(self); self.kcppTimeoutSpin.setRange(5, 600); self.kcppTimeoutSpin.setValue(self.KCPP_DEFAULT_TIMEOUT)
        self.kcppAutoContinue = QCheckBox("Auto-continue until JSON is complete", self); self.kcppAutoContinue.setChecked(True)

        kcppForm.addWidget(QLabel("Chunk size (tokens)"), 0, 0); kcppForm.addWidget(self.kcppChunkSpin, 0, 1)
        kcppForm.addWidget(QLabel("Max chunks"), 1, 0); kcppForm.addWidget(self.kcppMaxPassesSpin, 1, 1)
        kcppForm.addWidget(QLabel("Timeout per chunk (s)"), 2, 0); kcppForm.addWidget(self.kcppTimeoutSpin, 2, 1)
        kcppForm.addWidget(self.kcppAutoContinue, 3, 0, 1, 2)

        # Prompt editor
        promptBox = QGroupBox("Instruction Prompt")
        promptLay = QVBoxLayout(promptBox)
        root.addWidget(promptBox)

        self.promptEdit = SpellCheckPlainTextEdit(self)
        self.promptEdit.setPlainText("")
        btnRow = QHBoxLayout()
        self.btnLoadDefault = QPushButton("Load ChatGPT Rich Prompt")
        self.btnLoadDefault.clicked.connect(lambda: self.promptEdit.setPlainText(CHATGPT_DEFAULT_PROMPT))
        self.btnSavePrompt = QPushButton("Save Prompt…"); self.btnSavePrompt.clicked.connect(self._save_prompt)
        self.btnLoadPrompt = QPushButton("Load Prompt…"); self.btnLoadPrompt.clicked.connect(self._load_prompt)
        btnRow.addWidget(self.btnLoadDefault); btnRow.addStretch(1); btnRow.addWidget(self.btnLoadPrompt); btnRow.addWidget(self.btnSavePrompt)
        promptLay.addLayout(btnRow); promptLay.addWidget(self.promptEdit)

        # Brief
        briefBox = QGroupBox("Your Character or World Brief")
        briefLay = QVBoxLayout(briefBox)
        root.addWidget(briefBox)
        self.briefEdit = SpellCheckPlainTextEdit(self)
        self.briefEdit.setPlaceholderText("Example: Generate a character named Audrey…")
        briefLay.addWidget(self.briefEdit)

        # Options
        optRow = QHBoxLayout()
        self.onlyFillEmpty = QCheckBox("Fill only empty fields", self); self.onlyFillEmpty.setChecked(False)
        optRow.addWidget(self.onlyFillEmpty); optRow.addStretch(1)
        root.addLayout(optRow)

        # Actions (correct buttons for this tab)
        actionRow = QHBoxLayout()
        self.runButton = QPushButton("Generate and Populate Fields")
        self.runButton.clicked.connect(self._run_generation)
        self.stopButton = QPushButton("Stop")
        self.stopButton.setEnabled(False)
        self.stopButton.clicked.connect(self._stop_generation)
        actionRow.addWidget(self.runButton)
        actionRow.addWidget(self.stopButton)
        root.addLayout(actionRow)

        # Raw output
        self.rawOut = QPlainTextEdit(self)
        self.rawOut.setReadOnly(True)
        self.rawOut.setMaximumHeight(140)
        self.rawOut.setPlaceholderText("Raw model output for debugging…")
        root.addWidget(self.rawOut)

        self._on_backend_change(0)


    def _on_backend_change(self, idx):
        if idx == 0:  # OpenAI (chat)
            if not self.baseUrlEdit.text().strip() or self.baseUrlEdit.text().startswith("http://localhost"):
                self.baseUrlEdit.setText(DEFAULT_OPENAI_BASE)
            if not self.modelEdit.text().strip():
                self.modelEdit.setText(DEFAULT_OPENAI_MODEL)  # <-- was incorrectly "gpt-image-1"
            self.kcppBox.setVisible(False)
        else:         # KoboldCPP (text)
            self.baseUrlEdit.setText(DEFAULT_KCPP_BASE)
            self.kcppBox.setVisible(True)
            # (Model field is ignored by /api/v1/generate; keep whatever is typed)


    # Prompt file helpers
    def _save_prompt(self):
        fileName, _ = QFileDialog.getSaveFileName(self, "Save Prompt", "prompt.txt", "Text Files (*.txt)")
        if fileName:
            try:
                with open(fileName, "w", encoding="utf-8") as f:
                    f.write(self.promptEdit.toPlainText())
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not save prompt:\n{e}")

    def _load_prompt(self):
        fileName, _ = QFileDialog.getOpenFileName(self, "Load Prompt", self.window().global_filepath, "Text Files (*.txt)")
        if fileName:
            try:
                with open(fileName, "r", encoding="utf-8") as f:
                    self.promptEdit.setPlainText(f.read())
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not load prompt:\n{e}")

    # Main action
    def _run_generation(self):
        if requests is None:
            QMessageBox.critical(self, "Missing Dependency", "The 'requests' package is required. Install with: pip install requests")
            return
        brief = self.briefEdit.toPlainText().strip()
        if not brief:
            QMessageBox.information(self, "No brief", "Please enter a short description of the character or world.")
            return
        prompt = self.promptEdit.toPlainText().strip() or CHATGPT_DEFAULT_PROMPT
        # Warn if we’re about to overwrite non-empty fields (unless "Fill only empty" is on)
        if not self.onlyFillEmpty.isChecked():
            ed = self.editor
            has_any_text = any([
                ed.descriptionEdit.toPlainText().strip(),
                ed.personalityEdit.toPlainText().strip(),
                ed.scenarioEdit.toPlainText().strip(),
                ed.firstMesEdit.toPlainText().strip(),
                ed.mesExampleEdit.toPlainText().strip(),
            ])
            if has_any_text:
                btn = QMessageBox.warning(
                    self,
                    "Overwrite existing fields?",
                    "This card already has text in one or more fields.\n"
                    "Generate & Populate will replace them.\n\nContinue?",
                    QMessageBox.Yes | QMessageBox.Cancel,
                    QMessageBox.Cancel
                )
                if btn != QMessageBox.Yes:
                    return


        try:
            if self.backendCombo.currentIndex() == 0:
                data = self._call_openai(prompt, brief)
            else:
                data = self._call_koboldcpp(prompt, brief)
        except Exception as e:
            QMessageBox.critical(self, "Generation Error", str(e))
            return

        if not data:
            QMessageBox.warning(self, "No data", "The model returned no usable JSON.")
            return

        # Populate fields
        ed = self.editor
        def set_if_ok(widget, text):
            if self.onlyFillEmpty.isChecked() and widget.toPlainText().strip():
                return
            widget.setPlainText(text or "")

        set_if_ok(ed.descriptionEdit, data.get("description", ""))
        set_if_ok(ed.personalityEdit, data.get("personality", ""))
        set_if_ok(ed.scenarioEdit, data.get("scenario", ""))
        set_if_ok(ed.firstMesEdit, data.get("first_mes", ""))
        set_if_ok(ed.mesExampleEdit, data.get("mes_example", ""))
        ed.setDirty()
        self.window().updateTokenCount()
        QMessageBox.information(self, "Done", "Fields populated. Review and Save when ready.")

    # Backend callers
    def _call_openai(self, prompt, brief):
        base = self.baseUrlEdit.text().rstrip('/')
        url = base + "/chat/completions"
        model = self.modelEdit.text().strip() or DEFAULT_OPENAI_MODEL
        headers = {
            "Content-Type": "application/json",
        }
        key = self.apiKeyEdit.text().strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": brief}
            ],
            "temperature": float(self.tempSpin.value()),
            "max_tokens": int(self.maxTokSpin.value()),
        }
        # If the API supports JSON mode, this helps enforce a clean object
        payload["response_format"] = {"type": "json_object"}
        r = requests.post(url, headers=headers, json=payload, timeout=120)
        if r.status_code >= 400:
            raise RuntimeError(f"OpenAI error {r.status_code}: {r.text[:500]}")
        out = r.json()
        text = None
        try:
            text = out["choices"][0]["message"]["content"]
        except Exception:
            # older style
            if "choices" in out and out["choices"]:
                text = out["choices"][0].get("text")
        self.rawOut.setPlainText(text or json.dumps(out, ensure_ascii=False, indent=2))
        return self._extract_json(text)

    def _call_koboldcpp(self, prompt, brief):
        """Entry point for KoboldCPP calls. Uses chunked continuation when enabled."""
        if self.kcppAutoContinue.isChecked():
            return self._call_koboldcpp_chunked(prompt, brief)

        # Fallback single-shot (short) for quick tests
        base = self.baseUrlEdit.text().rstrip('/')
        url = base + "/api/v1/generate"
        composed = (
            prompt + "\n\n"
            "User brief:\n" + brief + "\n\n"
            "Return ONLY a single JSON object with keys: description, personality, scenario, first_mes, mes_example.\n"
            "Start with '{' and end with '}'.\n"
        )
        payload = {
            "prompt": composed,
            "max_length": int(self.kcppChunkSpin.value()),  # keep short
            "temperature": float(self.tempSpin.value()),
        }
        r = requests.post(url, json=payload, timeout=int(self.kcppTimeoutSpin.value()))
        if r.status_code >= 400:
            raise RuntimeError(f"KoboldCPP error {r.status_code}: {r.text[:500]}")
        out = r.json()
        text = None
        if isinstance(out, dict):
            if "results" in out and out["results"]:
                text = out["results"][0].get("text")
            elif "text" in out:
                text = out.get("text")
        self.rawOut.setPlainText(text or json.dumps(out, ensure_ascii=False, indent=2))
        
        from PyQt5.QtWidgets import QApplication
        QApplication.processEvents()

        return self._extract_json(text)

    def _call_koboldcpp_chunked(self, prompt, brief):
        """
        Chunked generation for KoboldCPP using CONTEXT REPLAY:
          - Pass 1: instruction + brief
          - Pass N>1: resend instruction + the JSON produced so far (no 'continue' text)
        This keeps the model's context coherent across API calls.
        """
        from PyQt5.QtWidgets import QApplication

        base = self.baseUrlEdit.text().rstrip('/')
        gen_url = base + "/api/v1/generate"
        temp = float(self.tempSpin.value())
        chunk_tokens = int(self.kcppChunkSpin.value())
        max_passes = int(self.kcppMaxPassesSpin.value())
        timeout = int(self.kcppTimeoutSpin.value())

        self._stop_requested = False
        self.stopButton.setEnabled(True)
        try:
            # Instruction that teaches the output shape
            base_instruction = (
                prompt + "\n\n"
                "User brief:\n" + brief + "\n\n"
                "Return ONLY a single JSON object with keys exactly: description, personality, scenario, first_mes, mes_example.\n"
                "Do not wrap in code fences. Begin with '{' and end with '}'."
            )

            def post_once(ptext):
                payload = {
                    "prompt": ptext,
                    "max_length": chunk_tokens,
                    "temperature": temp,
                }
                r = requests.post(gen_url, json=payload, timeout=timeout)
                if r.status_code >= 400:
                    raise RuntimeError(f"KoboldCPP error {r.status_code}: {r.text[:500]}")
                out = r.json()
                t = None
                if isinstance(out, dict):
                    if "results" in out and out["results"]:
                        t = out["results"][0].get("text")
                    elif "text" in out:
                        t = out.get("text")
                return t or ""

            aggregated = ""

            # Pass 1
            if self._stop_requested:
                self._kcpp_abort()
                return None
            piece = post_once(base_instruction)
            aggregated += piece
            self.rawOut.setPlainText(aggregated)
            QApplication.processEvents()

            data = self._extract_json(aggregated)
            if data:
                return data

            # Subsequent passes: RESEND instruction + JSON-so-far to continue.
            for _ in range(2, max_passes + 1):
                if self._stop_requested:
                    self._kcpp_abort()
                    break

                # keep entire JSON-so-far in view; if huge, clip from first '{'
                jstart = aggregated.find('{')
                json_so_far = aggregated[jstart:] if jstart != -1 else aggregated

                continue_prompt = base_instruction + "\n" + json_so_far

                piece = post_once(continue_prompt)
                aggregated += piece
                self.rawOut.setPlainText(aggregated)
                QApplication.processEvents()

                data = self._extract_json(aggregated)
                if data:
                    return data

            # One last try (brace-fixes etc.)
            return self._extract_json(aggregated)
        finally:
            self.stopButton.setEnabled(False)

    # JSON extractor that is resilient to chatty outputs
    def _extract_json(self, text):
        if not text:
            return None
        text = text.strip()

        # 1) Fast path: whole string is valid JSON
        try:
            return json.loads(text)
        except Exception:
            pass

        # 2) Try the largest {...} slice we can find
        start = text.find('{')
        if start == -1:
            return None

        end = text.rfind('}')
        if end != -1 and end > start:
            fragment = text[start:end + 1]
        else:
            # No closing brace yet — we only have a leading fragment
            fragment = text[start:]

        # Try raw fragment
        try:
            return json.loads(fragment)
        except Exception:
            pass

        # 3) Remove trailing commas before } or ] (common LLM hiccup)
        fragment2 = re.sub(r',\s*([}\]])', r'\1', fragment)
        try:
            return json.loads(fragment2)
        except Exception:
            pass

        # 4) Brace-balance heuristic: if it looks almost done, add at most a few closing braces
        opens = fragment.count('{')
        closes = fragment.count('}')
        if opens > closes:
            missing = min(opens - closes, 3)
            try:
                return json.loads(fragment + ('}' * missing))
            except Exception:
                pass

        # Not parseable yet — we will request another chunk and try again
        return None


# ----------------- Character Image Generation -----------------

class ImageGenWidget(QWidget):
    """
    Generate PNG character images via
      - OpenAI Images API (gpt-image-1)
      - KoboldCPP Stable Diffusion (A1111-compatible /sdapi/v1/txt2img)
    """
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self._stop_requested = False  # reserved if you later add streaming/polling
        self._build_ui()

    def _build_ui(self):
        # Root: vertical splitter (top = controls in tabs, bottom = actions + thumbnails)
        root = QVBoxLayout(self)
        split = QSplitter(Qt.Vertical, self)
        root.addWidget(split)

        # ----- TOP: tabbed controls -----
        top = QWidget(self)
        topLay = QVBoxLayout(top)
        tabs = QTabWidget(self)
        topLay.addWidget(tabs)
        split.addWidget(top)

        # -------- Tab 1: Image Prompt --------
        tabPrompt = QWidget(self)
        pForm = QGridLayout(tabPrompt)

        self.syncChk = QCheckBox("Sync from Description when opening tab", self)
        self.syncChk.setChecked(True)

        self.promptEdit = QPlainTextEdit(self)
        self.promptEdit.setPlaceholderText("This will auto-fill from Description. You can edit freely.")
        self.negativeEdit = QPlainTextEdit(self)
        self.negativeEdit.setPlaceholderText("(Optional) Negative prompt, e.g., 'blurry, lowres, extra fingers'")

        self.numSpin = QSpinBox(self); self.numSpin.setRange(1, 8); self.numSpin.setValue(2)
        self.widthSpin = QSpinBox(self); self.widthSpin.setRange(256, 2048); self.widthSpin.setSingleStep(64); self.widthSpin.setValue(512)
        self.heightSpin = QSpinBox(self); self.heightSpin.setRange(256, 2048); self.heightSpin.setSingleStep(64); self.heightSpin.setValue(512)

        r = 0
        pForm.addWidget(self.syncChk, r, 0, 1, 2); r += 1
        pForm.addWidget(QLabel("Prompt"), r, 0); pForm.addWidget(self.promptEdit, r, 1); r += 1
        pForm.addWidget(QLabel("Negative"), r, 0); pForm.addWidget(self.negativeEdit, r, 1); r += 1
        pForm.addWidget(QLabel("Count"), r, 0); pForm.addWidget(self.numSpin, r, 1); r += 1
        pForm.addWidget(QLabel("Width × Height"), r, 0)
        wh = QWidget(self); whl = QHBoxLayout(wh); whl.setContentsMargins(0,0,0,0)
        whl.addWidget(self.widthSpin); whl.addWidget(QLabel("×")); whl.addWidget(self.heightSpin)
        pForm.addWidget(wh, r, 1); r += 1

        tabs.addTab(tabPrompt, "Image Prompt")

        # -------- Tab 2: Backend --------
        tabBackend = QWidget(self)
        bForm = QGridLayout(tabBackend)

        self.backendCombo = QComboBox(self)
        self.backendCombo.addItems(["OpenAI Images (gpt-image-1)", "KoboldCPP Stable Diffusion (txt2img)"])
        self.backendCombo.currentIndexChanged.connect(self._on_backend_change)

        self.baseUrlEdit = QLineEdit(DEFAULT_OPENAI_BASE, self)
        self.modelEdit   = QLineEdit("gpt-image-1", self)
        self.apiKeyEdit  = QLineEdit("", self); self.apiKeyEdit.setEchoMode(QLineEdit.Password)
        self.timeoutSpin = QSpinBox(self); self.timeoutSpin.setRange(10, 600); self.timeoutSpin.setValue(120)

        r = 0
        bForm.addWidget(QLabel("Provider"), r, 0); bForm.addWidget(self.backendCombo, r, 1); r += 1
        bForm.addWidget(QLabel("API Base URL"), r, 0); bForm.addWidget(self.baseUrlEdit, r, 1); r += 1
        bForm.addWidget(QLabel("Model / Engine"), r, 0); bForm.addWidget(self.modelEdit, r, 1); r += 1
        bForm.addWidget(QLabel("API Key"), r, 0); bForm.addWidget(self.apiKeyEdit, r, 1); r += 1
        bForm.addWidget(QLabel("Timeout (s)"), r, 0); bForm.addWidget(self.timeoutSpin, r, 1); r += 1

        tabs.addTab(tabBackend, "Backend")

        # -------- Tab 3: OpenAI Options --------
        tabOpenAI = QWidget(self)
        oForm = QGridLayout(tabOpenAI)

        self.qualityCombo = QComboBox(self); self.qualityCombo.addItems(["high", "medium", "low"])
        self.transparentChk = QCheckBox("Transparent background", self); self.transparentChk.setChecked(False)

        oForm.addWidget(QLabel("Quality"), 0, 0); oForm.addWidget(self.qualityCombo, 0, 1)
        oForm.addWidget(self.transparentChk, 1, 0, 1, 2)

        tabs.addTab(tabOpenAI, "OpenAI Options")

        # -------- Tab 4: KoboldCPP Options --------
        tabKCPP = QWidget(self)
        kForm = QGridLayout(tabKCPP)

        self.stepsSpin = QSpinBox(self); self.stepsSpin.setRange(5, 100); self.stepsSpin.setValue(28)
        self.cfgSpin   = QDoubleSpinBox(self); self.cfgSpin.setRange(1.0, 20.0); self.cfgSpin.setSingleStep(0.5); self.cfgSpin.setValue(7.0)
        self.samplerCombo = QComboBox(self)
        self.samplerCombo.addItems([
            "Euler a", "Euler", "LMS", "Heun",
            "DPM2", "DPM2 a",
            "DPM++ 2S a", "DPM++ 2M", "DPM++ SDE",
            "DDIM", "PLMS", "UniPC"
        ])
        self.samplerCombo.setEditable(True)  # allows typing a custom sampler if desired
        self.seedSpin  = QSpinBox(self); self.seedSpin.setRange(-1, 2**31-1); self.seedSpin.setValue(-1)

        kForm.addWidget(QLabel("Steps"), 0, 0); kForm.addWidget(self.stepsSpin, 0, 1)
        kForm.addWidget(QLabel("CFG Scale"), 1, 0); kForm.addWidget(self.cfgSpin, 1, 1)
        kForm.addWidget(QLabel("Sampler"), 2, 0); kForm.addWidget(self.samplerCombo, 2, 1)
        kForm.addWidget(QLabel("Seed (-1 = random)"), 3, 0); kForm.addWidget(self.seedSpin, 3, 1)

        tabs.addTab(tabKCPP, "KoboldCPP Options")

        # Make the tabs region roughly top third
        split.addWidget(top)
        split.setStretchFactor(0, 1)

        # ----- BOTTOM: actions + thumbnail gallery -----
        bottom = QWidget(self)
        botLay = QVBoxLayout(bottom)

        act = QHBoxLayout()
        self.generateBtn = QPushButton("Generate Images")
        self.generateBtn.clicked.connect(self._generate_clicked)
        self.clearBtn = QPushButton("Clear Thumbnails")
        self.clearBtn.clicked.connect(self._clear_thumbs)
        self.saveBtn = QPushButton("Save Selected…")
        self.saveBtn.clicked.connect(self._save_selected)
        self.useBtn = QPushButton("Set as Card Image…")
        self.useBtn.clicked.connect(self._use_selected_as_card)
        act.addWidget(self.generateBtn); act.addWidget(self.clearBtn)
        act.addStretch(1)
        act.addWidget(self.saveBtn); act.addWidget(self.useBtn)
        botLay.addLayout(act)

        self.thumbList = QListWidget(self)
        self.thumbList.setViewMode(QListWidget.IconMode)
        self.thumbList.setMovement(QListWidget.Static)
        self.thumbList.setWrapping(True)
        self.thumbList.setResizeMode(QListWidget.Adjust)
        
        self.thumbList.setSpacing(8)
        self.thumbList.setIconSize(QSize(180, 180))
        self.thumbList.setGridSize(QSize(188, 188))
        self.thumbList.setUniformItemSizes(True)
        self.thumbList.setSelectionMode(QAbstractItemView.SingleSelection)
        self.thumbList.setMinimumHeight(260)

        botLay.addWidget(self.thumbList, 1)

        split.addWidget(bottom)
        split.setStretchFactor(1, 2)
        # (Optional) give an initial 1/3–2/3 split
        try:
            split.setSizes([350, 750])
        except Exception:
            pass

        # Defaults per backend
        self._on_backend_change(0)


    # --- UI helpers ---
    def on_tab_selected(self):
        """Called by Editor when this tab becomes active."""
        if self.syncChk.isChecked():
            desc = self.editor.descriptionEdit.toPlainText().strip()
            if desc:
                # Gentle polish into an image prompt
                self.promptEdit.setPlainText(
                    desc + "\n\nStyle: cinematic portrait, soft key light, sharp focus, detailed textures, natural color, PNG output."
                )

    def _on_backend_change(self, idx):
        if idx == 0:
            # OpenAI Images
            if not self.baseUrlEdit.text().strip() or self.baseUrlEdit.text().startswith("http://localhost"):
                self.baseUrlEdit.setText(DEFAULT_OPENAI_BASE)
            self.modelEdit.setText(self.modelEdit.text() or "gpt-image-1")
        else:
            # KoboldCPP SD (A1111 compatible)
            self.baseUrlEdit.setText(DEFAULT_KCPP_BASE)
            if not self.modelEdit.text().strip():
                self.modelEdit.setText("stable-diffusion")  # label only; KoboldCPP doesn't require this field

    def _clear_thumbs(self):
        self.thumbList.clear()

    def _add_thumb(self, png_bytes, subtitle=""):
        pix = QPixmap()
        pix.loadFromData(png_bytes)
        icon = QIcon(pix)
        item = QListWidgetItem(icon, "")
        item.setData(Qt.UserRole, png_bytes)
        self.thumbList.addItem(item)

    def _selected_png(self):
        it = self.thumbList.currentItem()
        if not it:
            return None
        return it.data(Qt.UserRole)

    # --- Button actions ---
    def _generate_clicked(self):
        if requests is None:
            QMessageBox.critical(self, "Missing Dependency", "The 'requests' package is required. Install with: pip install requests")
            return
        prompt = self.promptEdit.toPlainText().strip()
        if not prompt:
            QMessageBox.information(self, "No prompt", "Please provide an image prompt (it auto-fills from Description).")
            return
        # Warn if current card already has an attached image
        ed = self.editor
        if getattr(ed, "filePath", None) and os.path.isfile(ed.filePath):
            btn = QMessageBox.warning(
                self,
                "Card already has an image",
                "The current card already has an attached image.\n"
                "Generate new images anyway? (You’ll still need to click "
                "“Set as Card Image…” to replace it.)",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel
            )
            if btn != QMessageBox.Yes:
                return

        try:
            if self.backendCombo.currentIndex() == 0:
                imgs = self._gen_openai_images(prompt)
            else:
                imgs = self._gen_kcpp_images(prompt)
        except Exception as e:
            QMessageBox.critical(self, "Image Generation Error", str(e))
            return

        if not imgs:
            QMessageBox.information(self, "No Images", "The backend returned no images.")
            return

        for i, b in enumerate(imgs, start=1):
            self._add_thumb(b, f"image {i}")

        QMessageBox.information(self, "Done", f"Generated {len(imgs)} image(s). Select one to save or attach to this card.")

    def _save_selected(self):
        png = self._selected_png()
        if not png:
            QMessageBox.information(self, "No Selection", "Select an image first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save Image As", "character.png", "PNG Images (*.png)")
        if not path:
            return
        with open(path, "wb") as f:
            f.write(png)
        QMessageBox.information(self, "Saved", f"Saved to:\n{path}")

    def _use_selected_as_card(self):
        png = self._selected_png()
        if not png:
            QMessageBox.information(self, "No Selection", "Select an image first.")
            return

        # Choose destination PNG and write metadata
        default_dest = os.path.join(self.window().global_filepath, self.editor.suggest_filename_from_name())
        dest_path, _ = QFileDialog.getSaveFileName(self, "Save Card PNG As", default_dest, "PNG Images (*.png)")
        if not dest_path:
            return

        try:
            with open(dest_path, "wb") as f:
                f.write(png)
            # Attach existing card metadata
            self.editor.updateDataFromUI()
            write_character(dest_path, self.editor.fullData)

            # Adopt this image into the editor
            self.editor.filePath = dest_path
            self.editor.is_virtual = False
            self.editor.refreshButtonStates()

            # Refresh file list
            self.window().imageList.updateDirectory()
            self.window().updateStack()
            QMessageBox.information(self, "Attached", "Image saved and attached to this card.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to attach image:\n{e}")

    # --- Backends ---

    def _gen_openai_images(self, prompt):
        """
        Uses OpenAI Images API (gpt-image-1).
        Returns: [png_bytes, ...]
        """
        base = self.baseUrlEdit.text().rstrip("/")
        url = base + "/images/generations"
        headers = {"Content-Type": "application/json"}
        key = self.apiKeyEdit.text().strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"

        size = f"{self.widthSpin.value()}x{self.heightSpin.value()}"
        n = int(self.numSpin.value())

        payload = {
            "model": self.modelEdit.text().strip() or "gpt-image-1",
            "prompt": prompt,
            "size": size,
            "n": n,
            "quality": self.qualityCombo.currentText(),  # drop automatically if not accepted
        }
        if self.transparentChk.isChecked():
            payload["background"] = "transparent"  # drop automatically if not accepted

        def do_request(p):
            r = requests.post(url, headers=headers, json=p, timeout=int(self.timeoutSpin.value()))
            if r.status_code >= 400:
                # If the error says unknown parameter, remove and retry once
                try:
                    err = r.json().get("error", {})
                    bad_param = (err.get("param") or "").lower()
                except Exception:
                    bad_param = ""
                if r.status_code == 400 and bad_param in {"quality", "background"} and bad_param in p:
                    p = dict(p); p.pop(bad_param, None)
                    r = requests.post(url, headers=headers, json=p, timeout=int(self.timeoutSpin.value()))
                    if r.status_code >= 400:
                        raise RuntimeError(f"OpenAI error {r.status_code}: {r.text[:500]}")
                    return r.json()
                raise RuntimeError(f"OpenAI error {r.status_code}: {r.text[:500]}")
            return r.json()

        out = do_request(payload)

        imgs = []
        for d in out.get("data", []):
            b64 = d.get("b64_json")
            if b64:
                imgs.append(base64.b64decode(b64))
                continue
            u = d.get("url")
            if u:
                try:
                    dl = requests.get(u, timeout=int(self.timeoutSpin.value()))
                    if dl.status_code == 200:
                        imgs.append(dl.content)
                except Exception:
                    pass
        return imgs

    def _gen_kcpp_images(self, prompt):
        """
        KoboldCPP Stable Diffusion via /sdapi/v1/txt2img
        Honor the UI 'Count' by:
          - first trying one request with n_iter = Count (batch_size = 1),
          - then, if needed, making additional requests until we have enough.
        Returns: [png_bytes, ...]
        """
        base = self.baseUrlEdit.text().rstrip("/")
        url = base + "/sdapi/v1/txt2img"

        want = int(self.numSpin.value())
        imgs = []

        # Common payload bits
        common = {
            "prompt": prompt,
            "negative_prompt": self.negativeEdit.toPlainText(),
            "steps": int(self.stepsSpin.value()),
            "cfg_scale": float(self.cfgSpin.value()),
            "sampler_name": self.samplerCombo.currentText().strip() or "Euler a",
            "width": int(self.widthSpin.value()),
            "height": int(self.heightSpin.value()),
            "seed": int(self.seedSpin.value()),
        }

        # ---- Pass 1: Try to get them all in one request (n_iter)
        first_payload = dict(common)
        first_payload["batch_size"] = 1
        first_payload["n_iter"] = want

        r = requests.post(url, json=first_payload, timeout=int(self.timeoutSpin.value()))
        if r.status_code >= 400:
            raise RuntimeError(f"KoboldCPP SD error {r.status_code}: {r.text[:500]}")
        out = r.json()

        # Collect any images the server returned
        seq = out.get("images") or []
        for s in seq:
            try:
                imgs.append(base64.b64decode(s.split(",", 1)[-1] if isinstance(s, str) else s))
            except Exception:
                pass

        # ---- Pass 2: If still short, loop additional requests until satisfied
        # Some servers ignore n_iter and/or batch_size; this backfills.
        attempts = 0
        max_attempts = max(3, want * 3)
        while len(imgs) < want and attempts < max_attempts:
            attempts += 1
            remaining = want - len(imgs)

            payload = dict(common)
            payload["batch_size"] = 1
            payload["n_iter"] = min(4, remaining)

            r = requests.post(url, json=payload, timeout=int(self.timeoutSpin.value()))
            if r.status_code >= 400:
                raise RuntimeError(f"KoboldCPP SD error {r.status_code}: {r.text[:500]}")
            out = r.json()

            seq = out.get("images") or []
            got = 0
            for s in seq:
                if len(imgs) >= want:
                    break
                try:
                    imgs.append(base64.b64decode(s.split(",", 1)[-1] if isinstance(s, str) else s))
                    got += 1
                except Exception:
                    pass

            # If a server keeps returning nothing, don't loop forever
            if got == 0 and "images" in out:
                break

        return imgs[:want]


# --- helpers for thumbnail list (insert before ImageList) ---

class AspectRatioLabel(QLabel):
    """Scaled image label that preserves aspect ratio inside a fixed box."""
    def __init__(self, pixmap_path):
        super().__init__()
        self._pixmap = QPixmap(pixmap_path)
        self.setStyleSheet("background-color: transparent;")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box = self.size()
        scaled = self._pixmap.scaled(box, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        x = int((box.width() - scaled.width()) / 2)
        y = int((box.height() - scaled.height()) / 2)
        painter.drawPixmap(x, y, scaled)


class ImageThumbnail(QWidget):
    """Thumbnail row item: image on the left, name + filename on the right."""
    def __init__(self, imagePath, data):
        super().__init__()
        layout = QHBoxLayout()
        self.setLayout(layout)

        img = AspectRatioLabel(imagePath)
        img.setFixedSize(QSize(160, 160))
        layout.addWidget(img)

        text = QWidget(self)
        text_layout = QVBoxLayout(text)
        layout.addWidget(text)

        nameLabel = QLabel(data["data"].get("name", "") or "(unnamed)", text)
        fileLabel = QLabel(os.path.basename(imagePath), text)
        # Let long names wrap instead of clipping
        nameLabel.setWordWrap(True)
        fileLabel.setWordWrap(True)
        nameLabel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        fileLabel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        text_layout.addWidget(nameLabel)
        text_layout.addWidget(fileLabel)


# ----------------- Reworked Editor: image optional, replacable -----------------

class EditorWidget(QWidget):
    """
    Editor for a single card's data. Supports:
      - Virtual cards (no image yet)
      - Save to Image…
      - Change Image… (safe swap)
      - AI-Assisted Generation (new)
    """
    def __init__(self, fullData, filePath, itemLabel, parent=None, is_virtual=False):
        super().__init__(parent)

        self.fullData = fullData
        self.filePath = filePath  # may be None for virtual cards
        self.itemLabel = itemLabel
        self.initializing = True
        self.is_virtual = is_virtual

        self.tab_widget = QTabWidget(self)

        self.tabCommon = QWidget(self.tab_widget)
        self.tabUncommon = QWidget(self.tab_widget)
        self.tabCharacterBook = QWidget(self.tab_widget)
        self.tabAI = QWidget(self.tab_widget)
        self.tab_widget.addTab(self.tabCommon, "Common Fields")
        self.tab_widget.addTab(self.tabUncommon, "Uncommon Fields")
        self.tab_widget.addTab(self.tabCharacterBook, "Character Book")
        self.tab_widget.addTab(self.tabAI, "AI-Assisted Generation")

        # Common tab
        self.tabCommon_layout = QFormLayout(self.tabCommon)
        self.nameEdit = QLineEdit()
        self.nameEdit.setToolTip("Keep it short. The user may type it often.")
        self.nameEdit.textChanged.connect(self.setDirty)
        self.tabCommon_layout.addRow("Name", self.nameEdit)

        self.descriptionEdit = SpellCheckPlainTextEdit()
        self.descriptionEdit.setToolTip("Important description sent frequently. Keep it concise but thorough.")
        self.descriptionEdit.textChanged.connect(self.setDirty)
        self.tabCommon_layout.addRow("Description", self.descriptionEdit)

        self.personalityEdit = SpellCheckPlainTextEdit()
        self.personalityEdit.setToolTip("Brief summary of the character's personality.")
        self.personalityEdit.textChanged.connect(self.setDirty)
        self.tabCommon_layout.addRow("Personality", self.personalityEdit)

        self.scenarioEdit = SpellCheckPlainTextEdit()
        self.scenarioEdit.setToolTip("Brief summary of current situation.")
        self.scenarioEdit.textChanged.connect(self.setDirty)
        self.tabCommon_layout.addRow("Scenario", self.scenarioEdit)

        self.firstMesEdit = SpellCheckPlainTextEdit()
        self.firstMesEdit.setToolTip("Opening line written as the bot. Avoid writing the user's lines.")
        self.firstMesEdit.textChanged.connect(self.setDirty)
        self.tabCommon_layout.addRow("First Message", self.firstMesEdit)

        self.mesExampleEdit = SpellCheckPlainTextEdit()
        self.mesExampleEdit.setToolTip("A couple of example exchanges. Influences style until context fills.")
        self.mesExampleEdit.textChanged.connect(self.setDirty)
        self.tabCommon_layout.addRow("Message Example", self.mesExampleEdit)

        # Uncommon tab
        self.tabUncommon_layout = QGridLayout(self.tabUncommon)

        self.tabUncommon_layout.addWidget(QLabel("Alternate Greetings", self.tabUncommon), 0, 0)
        self.alternateGreetingsList = QListWidget(self.tabUncommon)
        self.alternateGreetingsList.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.alternateGreetingsList.setToolTip("Optional alternative first messages.")
        self.tabUncommon_layout.addWidget(self.alternateGreetingsList, 0, 1, 1, 3)
        self.addAlternateGreetingButton = QPushButton("Add Alternate Greeting", self.tabUncommon)
        self.addAlternateGreetingButton.clicked.connect(self.add_alternate_greeting)
        self.tabUncommon_layout.addWidget(self.addAlternateGreetingButton, 1, 1, 1, 3)

        self.tabUncommon_layout.addWidget(QLabel("System Prompt", self.tabUncommon), 2, 0)
        self.systemPromptEdit = SpellCheckPlainTextEdit(self.tabUncommon)
        self.systemPromptEdit.textChanged.connect(self.setDirty)
        self.tabUncommon_layout.addWidget(self.systemPromptEdit, 2, 1, 1, 3)

        self.tabUncommon_layout.addWidget(QLabel("Post History Instructions", self.tabUncommon), 3, 0)
        self.postHistoryInstructionsEdit = SpellCheckPlainTextEdit(self.tabUncommon)
        self.postHistoryInstructionsEdit.textChanged.connect(self.setDirty)
        self.tabUncommon_layout.addWidget(self.postHistoryInstructionsEdit, 3, 1, 1, 3)

        self.tabUncommon_layout.addWidget(QLabel("Tags", self.tabUncommon), 4, 0)
        self.tagsList = QLineEdit(self.tabUncommon)
        self.tagsList.setToolTip("comma, separated, list, of, tags")
        self.tagsList.textChanged.connect(self.setDirty)
        self.tabUncommon_layout.addWidget(self.tagsList, 4, 1, 1, 3)

        self.tabUncommon_layout.addWidget(QLabel("Character Version", self.tabUncommon), 5, 0)
        self.characterVersionEdit = QLineEdit(self.tabUncommon)
        self.characterVersionEdit.textChanged.connect(self.setDirty)
        self.tabUncommon_layout.addWidget(self.characterVersionEdit, 5, 1)

        self.tabUncommon_layout.addWidget(QLabel("Creator", self.tabUncommon), 5, 2)
        self.creatorEdit = QLineEdit(self.tabUncommon)
        self.creatorEdit.textChanged.connect(self.setDirty)
        self.tabUncommon_layout.addWidget(self.creatorEdit, 5, 3)

        self.tabUncommon_layout.addWidget(QLabel("Creator Notes", self.tabUncommon), 6, 0)
        self.creatorNotesEdit = SpellCheckPlainTextEdit(self.tabUncommon)
        self.creatorNotesEdit.textChanged.connect(self.setDirty)
        self.tabUncommon_layout.addWidget(self.creatorNotesEdit, 6, 1, 1, 3)

        self.tabUncommon_layout.addWidget(QLabel("Extensions", self.tabUncommon), 7, 0)
        self.extensionsEdit = QPlainTextEdit(self.tabUncommon)
        self.extensionsEdit.setMaximumHeight(PLAINTEXT_EDITOR_MAX_HEIGHT)
        self.extensionsEdit.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.extensionsEdit.setToolTip("A block of JSON values used by non-standard chatbot extensions.")
        self.extensionsEdit.textChanged.connect(self.setDirty)
        self.tabUncommon_layout.addWidget(self.extensionsEdit, 7, 1, 1, 3)

        # Character Book tab
        self.tabCharacterBook_layout = QVBoxLayout(self.tabCharacterBook)
        self.characterBookEdit = CharacterBookWidget(self.fullData, self)
        self.tabCharacterBook_layout.addWidget(self.characterBookEdit)

        # AI tab
        self.tabAI_layout = QVBoxLayout(self.tabAI)
        self.aiWidget = AIAssistWidget(self)
        self.tabAI_layout.addWidget(self.aiWidget)

        self.tabImageGen = QWidget(self.tab_widget)
        self.tab_widget.addTab(self.tabImageGen, "Character Image Generation")

        # ... after setting up the other tabs ...
        self.tabImageGen_layout = QVBoxLayout(self.tabImageGen)
        self.imageGenWidget = ImageGenWidget(self)
        self.tabImageGen_layout.addWidget(self.imageGenWidget)

        # When switching tabs, auto-sync image prompt from Description
        self.tab_widget.currentChanged.connect(self._on_tab_changed)


        # Buttons row
        self.saveButton = QPushButton("Save")
        self.saveButton.setToolTip("Save metadata back to the current image")
        self.saveButton.clicked.connect(self.saveClicked)

        self.saveAsButton = QPushButton("Save to Image…")
        self.saveAsButton.setToolTip("Pick or create a PNG to attach this card to, then write metadata")
        self.saveAsButton.clicked.connect(self.saveToImageClicked)

        self.changeImageButton = QPushButton("Change Image…")
        self.changeImageButton.setToolTip("Swap the underlying PNG while preserving this card's data")
        self.changeImageButton.clicked.connect(self.changeImageClicked)

        self.exportButton = QPushButton('Export JSON')
        self.exportButton.setToolTip("Save the card data as JSON (does not touch the PNG)")
        self.exportButton.clicked.connect(self.exportClicked)

        self.importButton = QPushButton('Import JSON')
        self.importButton.setToolTip("Load card data from JSON into the editor (click Save to write to PNG)")
        self.importButton.clicked.connect(self.importClicked)

        self.button_layout = QHBoxLayout()
        self.button_layout.addWidget(self.saveButton)
        self.button_layout.addWidget(self.saveAsButton)
        self.button_layout.addWidget(self.changeImageButton)
        self.button_layout.addWidget(self.exportButton)
        self.button_layout.addWidget(self.importButton)

        self.root_layout = QVBoxLayout(self)
        self.root_layout.addWidget(self.tab_widget)
        self.root_layout.addLayout(self.button_layout)
        self.setLayout(self.root_layout)

        self.updateUIFromData()
        self.refreshButtonStates()
        self.initializing = None

    # ----- data plumbing -----

    def updateUIFromData(self):
        data = self.fullData["data"]
        self.nameEdit.setText(data.get("name"))
        self.descriptionEdit.setPlainText(data.get("description"))
        self.personalityEdit.setPlainText(data.get("personality"))
        self.scenarioEdit.setPlainText(data.get("scenario"))
        self.firstMesEdit.setPlainText(data.get("first_mes"))
        self.mesExampleEdit.setPlainText(data.get("mes_example"))

        self.alternateGreetingsList.clear()
        for greeting in data.get("alternate_greetings", []):
            self.add_alternate_greeting(greeting)

        self.systemPromptEdit.setPlainText(data.get("system_prompt"))
        self.postHistoryInstructionsEdit.setPlainText(data.get("post_history_instructions"))
        self.tagsList.setText(", ".join(data.get("tags", [])))
        self.characterVersionEdit.setText(data.get("character_version"))
        self.creatorEdit.setText(data.get("creator"))
        self.creatorNotesEdit.setPlainText(data.get("creator_notes"))
        self.extensionsEdit.setPlainText(json.dumps(data.get("extensions")))

        self.characterBookEdit.updateUIFromData()

    def updateDataFromUI(self):
        fullData = self.fullData
        data = fullData["data"]

        data["name"] = str(self.nameEdit.text())
        data["tags"] = [x.strip() for x in str(self.tagsList.text()).split(',') if x.strip() != ""]
        data["character_version"] = str(self.characterVersionEdit.text())
        data["description"] = str(self.descriptionEdit.toPlainText())
        data["personality"] = str(self.personalityEdit.toPlainText())
        data["scenario"] = str(self.scenarioEdit.toPlainText())
        data["first_mes"] = str(self.firstMesEdit.toPlainText())
        data["mes_example"] = str(self.mesExampleEdit.toPlainText())

        alternateGreetings = []
        for i in range(self.alternateGreetingsList.count()):
            item = self.alternateGreetingsList.item(i)
            greeting = self.alternateGreetingsList.itemWidget(item)
            alternateGreetings.append(greeting.editor.toPlainText())
        data["alternate_greetings"] = alternateGreetings
        data["system_prompt"] = str(self.systemPromptEdit.toPlainText())
        data["post_history_instructions"] = str(self.postHistoryInstructionsEdit.toPlainText())
        data["creator"] = str(self.creatorEdit.text())
        data["creator_notes"] = str(self.creatorNotesEdit.toPlainText())
        data["extensions"] = safeJSONLoads(self.extensionsEdit.toPlainText())

        self.characterBookEdit.updateDataFromUI()

    def _on_tab_changed(self, idx):
        # When the Image tab becomes active, populate its prompt (if desired)
        try:
            w = self.tab_widget.widget(idx)
            if w is self.tabImageGen and hasattr(self, "imageGenWidget"):
                self.imageGenWidget.on_tab_selected()
        except Exception:
            pass

    # ----- virtual + buttons -----

    def refreshButtonStates(self):
        has_image = self.filePath is not None and os.path.isfile(self.filePath) and self.filePath.lower().endswith(".png")
        self.saveButton.setEnabled(has_image)
        self.changeImageButton.setEnabled(has_image)

    def saveClicked(self):
        if not self.filePath:
            QMessageBox.information(self, "No Image", "This card is not attached to an image yet. Use “Save to Image…”")
            return
        self.updateDataFromUI()
        write_character(self.filePath, self.fullData)
        if self.itemLabel:
            self.itemLabel.setStyleSheet("")
        self.window().updateTokenCount()

    def suggest_filename_from_name(self):
        name = self.fullData["data"].get("name", "").strip()
        if not name:
            name = "untitled_card"
        safe = "".join(ch if ch.isalnum() or ch in (" ", "_", "-") else "_" for ch in name).strip().replace(" ", "_")
        return f"{safe}.png"

    def saveToImageClicked(self):
        src_path, _ = QFileDialog.getOpenFileName(self, "Choose Image to Attach", self.window().global_filepath, "PNG Images (*.png)")
        if not src_path:
            return
        default_dest = os.path.join(self.window().global_filepath, self.suggest_filename_from_name())
        dest_path, _ = QFileDialog.getSaveFileName(self, "Save Card PNG As", default_dest, "PNG Images (*.png)")
        if not dest_path:
            return

        try:
            shutil.copyfile(src_path, dest_path)
            self.updateDataFromUI()
            write_character(dest_path, self.fullData)

            self.filePath = dest_path
            self.is_virtual = False
            self.refreshButtonStates()

            self.window().imageList.updateDirectory()
            self.window().updateStack()
            self.window().updateTokenCount()
            QMessageBox.information(self, "Saved", f"Card saved to:\n{dest_path}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save card:\n{e}")

    def changeImageClicked(self):
        if not self.filePath or not os.path.isfile(self.filePath):
            QMessageBox.information(self, "No Image", "This card isn't attached to a PNG yet. Use “Save to Image…”")
            return

        new_png, _ = QFileDialog.getOpenFileName(self, "Select New PNG", self.window().global_filepath, "PNG Images (*.png)")
        if not new_png:
            return

        original = self.filePath
        bak = original + ".bak"

        try:
            if os.path.exists(bak):
                os.remove(bak)
            os.rename(original, bak)
            shutil.copyfile(new_png, original)
            self.updateDataFromUI()
            write_character(original, self.fullData)
            os.remove(bak)

            self.window().imageList.updateDirectory()
            self.window().updateStack()
            QMessageBox.information(self, "Image Changed", "The image has been swapped successfully.")
        except Exception as e:
            try:
                if os.path.exists(bak):
                    if os.path.exists(original):
                        os.remove(original)
                    os.rename(bak, original)
            except Exception:
                pass
            QMessageBox.critical(self, "Error", f"Failed to change image:\n{e}")

    def exportClicked(self):
        self.updateDataFromUI()
        jsonFilepath = (self.filePath[:-3] + "json") if self.filePath else os.path.join(self.window().global_filepath, "card.json")
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        fileName, _ = QFileDialog.getSaveFileName(self, "Export Card JSON", jsonFilepath, "JSON Files (*.json)", options=options)
        if fileName:
            with open(fileName, "w", encoding="utf-8") as f:
                json.dump(self.fullData, f, ensure_ascii=False, indent=2)

    def importClicked(self):
        options = QFileDialog.Options()
        options |= QFileDialog.ReadOnly
        filepath = self.window().global_filepath
        fileName, _ = QFileDialog.getOpenFileName(self, "Import Card JSON", filepath, "JSON Files (*.json)", options=options)
        if fileName:
            with open(fileName, "r", encoding="utf-8") as f:
                self.fullData = json.load(f)
        self.updateUIFromData()
        self.setDirty()

    def add_alternate_greeting(self, text=None):
        widget_item = QListWidgetItem(self.alternateGreetingsList)
        custom_widget = AlternateGreetingWidget(self)
        if text:
            custom_widget.editor.setPlainText(text)
        widget_item.setSizeHint(custom_widget.sizeHint())
        self.alternateGreetingsList.addItem(widget_item)
        self.alternateGreetingsList.setItemWidget(widget_item, custom_widget)
        custom_widget.delete_button.clicked.connect(lambda: self.delete_alternate_greeting(widget_item))
        self.setDirty()

    def delete_alternate_greeting(self, item):
        row = self.alternateGreetingsList.row(item)
        self.alternateGreetingsList.takeItem(row)
        self.setDirty()

    def setDirty(self):
        if not self.initializing and self.itemLabel:
            self.itemLabel.setStyleSheet(DIRTY_CHARACTER_COLOUR)


# ----------------- Image list with support for creating virtual cards -----------------

class ImageList(QListWidget):
    directoryChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.itemClicked.connect(self.showImage)
        self.stack = None
        self.virtual_count = 0
        self.loadImages()

    def loadImages(self):
        self.clear()
        old_stack = self.stack
        self.stack = QStackedWidget()
        if old_stack is not None:
            old_stack.deleteLater()

        filepath = self.window().global_filepath
        for file in sorted(os.listdir(filepath)):
            if file.lower().endswith(".png"):
                item = QListWidgetItem(self)
                self.addItem(item)
                imagePath = os.path.join(filepath, file)
                data = read_character(imagePath)
                imageLabel = ImageThumbnail(imagePath, data)
                item.setSizeHint(imageLabel.sizeHint())
                self.setItemWidget(item, imageLabel)
                self.stack.addWidget(EditorWidget(data, imagePath, imageLabel, self, is_virtual=False))

        self.virtual_count = 0

    def showImage(self, item):
        index = self.row(item)
        self.stack.setCurrentIndex(index)
        self.stack.currentWidget().show()
        self.window().updateTokenCount()

    def changeDirectory(self):
        newDirpath = QFileDialog.getExistingDirectory(self, "Select Directory")
        if newDirpath != '':
            self.window().global_filepath = newDirpath
            self.updateDirectory()

    def updateDirectory(self):
        self.loadImages()
        self.directoryChanged.emit()

    def addVirtualCard(self, card_data):
        label = QWidget(self)
        hl = QHBoxLayout(label)
        title = QLabel(card_data["data"].get("name", "Untitled (not saved)"), label)
        subtitle = QLabel("[virtual card] (attach an image via 'Save to Image…')", label)
        block = QWidget(label)
        bl = QVBoxLayout(block)
        bl.addWidget(title)
        bl.addWidget(subtitle)
        hl.addWidget(block)
        label.setLayout(hl)

        item = QListWidgetItem(self)
        item.setSizeHint(label.sizeHint())
        self.addItem(item)
        self.setItemWidget(item, label)
        self.stack.addWidget(EditorWidget(card_data, None, label, self, is_virtual=True))
        self.setCurrentItem(item)
        self.showImage(item)
        self.virtual_count += 1


# ----------------- Main Window -----------------

class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI Char Card Editor v0.4")
        self.global_filepath = "."
        self.layout = QVBoxLayout()
        self.setLayout(self.layout)

        self.splitter = QSplitter(Qt.Horizontal)
        self.layout.addWidget(self.splitter)

        self.imageList = ImageList(self)
        self.imageList.directoryChanged.connect(self.updateStack)

        # Right panel: controls + thumbnails
        self.changeDirButton = QPushButton("Change Directory", self)
        self.changeDirButton.setToolTip("Switches thumbnail list to another directory.\nSave your work first.")
        self.changeDirButton.clicked.connect(self.imageList.changeDirectory)

        self.refreshDirButton = QPushButton("Refresh", self)
        self.refreshDirButton.setToolTip("Reloads the thumbnail list for the current directory.\nSave your work first.")
        self.refreshDirButton.clicked.connect(self.imageList.updateDirectory)

        self.newCardButton = QPushButton("New Card", self)
        self.newCardButton.setToolTip("Create a new blank card with no image attached yet.")
        self.newCardButton.clicked.connect(self.createNewCard)

        self.rightPanel = QWidget()
        self.rightPanelLayout = QVBoxLayout()
        self.rightPanel.setLayout(self.rightPanelLayout)
        self.rightPanelLayout.addWidget(self.changeDirButton)
        self.rightPanelLayout.addWidget(self.refreshDirButton)
        self.rightPanelLayout.addWidget(self.newCardButton)
        self.rightPanelLayout.addWidget(self.imageList)

        # Left pane: current editor stack
        self.splitter.addWidget(self.imageList.stack)
        self.splitter.addWidget(self.rightPanel)
        self.splitter.setStretchFactor(0, 2)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([800, 400])     # keep the default 2/3 : 1/3 feel


        self.token_count_label = QLabel("Est. Tokens: 0", self)
        self.vocab_selector = QComboBox(self)
        self.vocab_selector.addItem("Big Vocab (6 char/token)")
        self.vocab_selector.addItem("Small Vocab (4 char/token)")
        self.vocab_selector.currentIndexChanged.connect(self.updateTokenCount)

        self.refresh_button = QPushButton("Refresh Count", self)
        self.refresh_button.clicked.connect(self.updateTokenCount)

        self.appearance_label = QLabel("Appearance:", self)
        self.appearance_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.dark_mode_dropdown = QComboBox(self)
        self.dark_mode_dropdown.addItem("Light Mode")
        self.dark_mode_dropdown.addItem("Dark Mode")
        self.dark_mode_dropdown.currentIndexChanged.connect(self.toggleDarkMode)

        self.font_size_dropdown = QComboBox(self)
        for size in range(8, 25, 2):
            self.font_size_dropdown.addItem(f"{size}pt")
        self.font_size_dropdown.currentIndexChanged.connect(self.changeFontSize)
        
        self.lang_label = QLabel("Spellcheck:", self)
        self.lang_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        
        self.resize(1200, 900)
        self.setMinimumSize(800, 600)


        self.lang_dropdown = QComboBox(self)
        self.lang_dropdown.setEnabled(enchant is not None)
        langs = available_spell_langs() or [DEFAULT_SPELL_LANG]
        self.lang_dropdown.addItems(langs)
        self.lang_dropdown.currentTextChanged.connect(self.applySpellLanguage)

        self.token_layout = QHBoxLayout()
        self.token_layout.addWidget(self.token_count_label)
        self.token_layout.addWidget(self.vocab_selector)
        self.token_layout.addWidget(self.refresh_button)
        self.token_layout.addWidget(self.appearance_label)
        self.token_layout.addSpacing(2)
        self.token_layout.addWidget(self.dark_mode_dropdown)
        self.token_layout.addWidget(self.font_size_dropdown)
        self.layout.addLayout(self.token_layout)
        self.token_layout.addWidget(self.lang_label)
        self.token_layout.addWidget(self.lang_dropdown)
        # Start on a blank, virtual card instead of auto-selecting the first file.
        self.createNewCard(ask_name=False)


    def createNewCard(self, checked=False, ask_name=True):
        card = deep_empty_card()
        if ask_name:
            name, ok = QInputDialog.getText(self, "New Card", "Card name (optional):")
            if ok and name.strip():
                card["data"]["name"] = name.strip()
        self.imageList.addVirtualCard(card)

    def toggleDarkMode(self, index):
        dark_mode = (index == 1)
        if dark_mode:
            self.setStyleSheet("""
                QWidget { background-color: #333; color: white; }
                QLineEdit, QPlainTextEdit, QListWidget { background-color: #444; color: white; border: 1px solid #555; }
                QPushButton { background-color: #555; color: white; border: 1px solid #666; }
                QComboBox { background-color: #444; color: white; border: 1px solid #555; }
                QComboBox::drop-down { background-color: #555; color: white; }
                QTabWidget::pane { border: none; } 
                QTabWidget::tab-bar { alignment: center; }
                QTabBar::tab { background: #444; color: white; padding: 5px; border: 1px solid #555; }
                QTabBar::tab:selected { background: #555; }
            """)
        else:
            self.setStyleSheet("")

    def changeFontSize(self, index):
        font_size = 8 + index * 2
        font = self.font()
        font.setPointSize(font_size)
        self.setFont(font)
        for child in self.findChildren(QWidget):
            child.setFont(font)
        self.imageList.updateGeometry()
        
    def applySpellLanguage(self, lang):
        if enchant is None:
            return
        editors = self.findChildren(SpellCheckPlainTextEdit)
        for e in editors:
            e.blockSignals(True)
        try:
            for e in editors:
                e.setLanguage(lang)
        finally:
            for e in editors:
                e.blockSignals(False)

    def updateStack(self):
        new_stack = self.imageList.stack
        if self.splitter.count() == 0:
            self.splitter.addWidget(new_stack)
            self.splitter.addWidget(self.rightPanel)
        else:
            self.splitter.replaceWidget(0, new_stack)
            
        self.splitter.setStretchFactor(0, 2)   # editor stack ~2/3
        self.splitter.setStretchFactor(1, 1)   # browser column ~1/3
        self.splitter.setSizes([800, 400])     # initial 1200px split

        self.updateTokenCount()

    def updateTokenCount(self):
        current_editor = self.imageList.stack.currentWidget()
        if current_editor:
            if hasattr(current_editor, "updateDataFromUI"):
                current_editor.updateDataFromUI()
            token_count = self.calculateTokenCount(current_editor.fullData["data"])
            self.token_count_label.setText(f"Est. Tokens: {token_count}")

    def calculateTokenCount(self, data):
        chars_per_token = 6 if self.vocab_selector.currentIndex() == 0 else 4
        total_chars = 0

        for key in ["name", "description", "personality", "scenario", "first_mes", "mes_example",
                    "system_prompt", "post_history_instructions", "creator_notes"]:
            total_chars += len(data.get(key, "") or "")

        total_chars += sum(len(greeting or "") for greeting in data.get("alternate_greetings", []))
        total_chars += sum(len(tag or "") for tag in data.get("tags", []))
        total_chars += len(data.get("creator", "") or "")
        total_chars += len(data.get("character_version", "") or "")

        if "character_book" in data and "entries" in data["character_book"]:
            for entry in data["character_book"]["entries"]:
                total_chars += len(entry.get("content", "") or "")
                total_chars += sum(len(key or "") for key in entry.get("keys", []))
                total_chars += len(entry.get("name", "") or "")
                total_chars += len(entry.get("comment", "") or "")
                total_chars += sum(len(key or "") for key in entry.get("secondary_keys", []))
                total_chars += len(json.dumps(entry.get("extensions", {})))

        total_chars += len(json.dumps(data.get("extensions", {})))
        return int(total_chars / chars_per_token)


# ----------------- main -----------------

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()

    if enchant is None:
        print("Note: pyenchant not found. Spellcheck is disabled. Install with: pip install pyenchant")
    if requests is None:
        print("Note: requests is not installed. AI-Assisted Generation will be disabled. Install with: pip install requests")

    sys.exit(app.exec_())

