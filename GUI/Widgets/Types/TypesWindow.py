from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from GUI.Session.session import TypeManager
from GUI.Utils import guiutils
from GUI.Widgets.Types.TypeEditorDialog import TypeEditorDialog
from libpince import typedefs
from tr.tr import TranslationConstants as tr


class TypesWindow(QWidget):
    """Manages user-defined alias/enum types. Structs are handled by StructuresWindow."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr.TYPES)
        self.setWindowFlags(Qt.WindowType.Window)
        self.resize(420, 360)

        layout = QVBoxLayout(self)
        self.treeWidget_Types = QTreeWidget(self)
        self.treeWidget_Types.setColumnCount(2)
        self.treeWidget_Types.setHeaderLabels([tr.TYPE_NAME, tr.TYPE_KIND])
        self.treeWidget_Types.setRootIsDecorated(False)
        layout.addWidget(self.treeWidget_Types)

        buttons = QHBoxLayout()
        self.pushButton_New = QPushButton(tr.NEW_TYPE, self)
        self.pushButton_Edit = QPushButton(tr.EDIT_TYPE, self)
        self.pushButton_Delete = QPushButton(tr.DELETE_TYPE, self)
        self.pushButton_Close = QPushButton(tr.CLOSE, self)
        for button in (self.pushButton_New, self.pushButton_Edit, self.pushButton_Delete):
            buttons.addWidget(button)
        buttons.addStretch()
        buttons.addWidget(self.pushButton_Close)
        layout.addLayout(buttons)

        self.pushButton_New.clicked.connect(self._new_type)
        self.pushButton_Edit.clicked.connect(self._edit_type)
        self.pushButton_Delete.clicked.connect(self._delete_type)
        self.pushButton_Close.clicked.connect(self.close)
        self.treeWidget_Types.itemDoubleClicked.connect(self._edit_type)

        self.refresh()
        guiutils.center_to_parent(self)

    def refresh(self) -> None:
        self.treeWidget_Types.clear()
        for name in TypeManager.list_names():
            definition = TypeManager.get(name)
            kind_text = tr.TYPE_KIND_ALIAS if isinstance(definition, typedefs.AliasType) else tr.TYPE_KIND_ENUM
            self.treeWidget_Types.addTopLevelItem(QTreeWidgetItem([name, kind_text]))
        self.treeWidget_Types.resizeColumnToContents(0)

    def _selected_name(self) -> str | None:
        item = self.treeWidget_Types.currentItem()
        return item.text(0) if item else None

    def _new_type(self) -> None:
        dialog = TypeEditorDialog(self)
        if not dialog.exec():
            return
        if not TypeManager.add(dialog.get_definition()):
            QMessageBox.warning(self, tr.ERROR, tr.TYPE_NAME_TAKEN)
            return
        self.parent().refresh_custom_type_ui()
        self.refresh()

    def _edit_type(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        definition = TypeManager.get(name)
        if definition is None:
            return
        dialog = TypeEditorDialog(self, definition)
        if not dialog.exec():
            return
        new_definition = dialog.get_definition()
        if new_definition.name != name:
            if not self.parent().rename_custom_type(name, new_definition.name):
                QMessageBox.warning(self, tr.ERROR, tr.TYPE_NAME_TAKEN)
                return
        TypeManager.update(new_definition)
        self.parent().refresh_custom_type_ui()
        self.refresh()

    def _delete_type(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        references = self.parent().custom_type_references(name)
        if references:
            QMessageBox.warning(self, tr.ERROR, tr.TYPE_IN_USE.format(name, "\n".join(references)))
            return
        if QMessageBox.question(self, tr.DELETE_TYPE, tr.DELETE_TYPE_PROMPT.format(name)) != QMessageBox.StandardButton.Yes:
            return
        TypeManager.delete(name)
        self.parent().refresh_custom_type_ui()
        self.refresh()
