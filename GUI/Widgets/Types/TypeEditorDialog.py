from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from GUI.Utils import guiutils
from GUI.Validators.HexValidator import HexValidator
from libpince import typedefs, utils
from tr.tr import TranslationConstants as tr


class TypeEditorDialog(QDialog):
    """Creates or edits an alias/enum type definition."""

    def __init__(self, parent: QWidget, definition: typedefs.AliasType | typedefs.EnumType | None = None) -> None:
        super().__init__(parent)
        self._original_name = definition.name if definition is not None else None
        self.setWindowTitle(tr.EDIT_TYPE if definition is not None else tr.NEW_TYPE)
        self._build_ui()
        if definition is not None:
            self._populate(definition)
        guiutils.center_to_parent(self)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        header = QFormLayout()
        self.lineEdit_Name = QLineEdit(self)
        header.addRow(tr.TYPE_NAME, self.lineEdit_Name)
        self.comboBox_Kind = QComboBox(self)
        self.comboBox_Kind.addItem(tr.TYPE_KIND_ALIAS, typedefs.AliasType.kind)
        self.comboBox_Kind.addItem(tr.TYPE_KIND_ENUM, typedefs.EnumType.kind)
        header.addRow(tr.TYPE_KIND, self.comboBox_Kind)
        layout.addLayout(header)

        self.stackedWidget = QStackedWidget(self)
        self.stackedWidget.addWidget(self._build_alias_page())
        self.stackedWidget.addWidget(self._build_enum_page())
        layout.addWidget(self.stackedWidget)

        self.buttonBox = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        layout.addWidget(self.buttonBox)
        self.buttonBox.accepted.connect(self.accept)
        self.buttonBox.rejected.connect(self.reject)

        self.comboBox_Kind.currentIndexChanged.connect(self._kind_changed)
        self._kind_changed()

    def _build_alias_page(self) -> QWidget:
        page = QWidget(self)
        form = QFormLayout(page)
        self.comboBox_ValueType = QComboBox(page)
        guiutils.fill_value_combobox(self.comboBox_ValueType, include_bit_field=True)
        form.addRow(tr.TYPE_UNDERLYING, self.comboBox_ValueType)

        self.lineEdit_Length = QLineEdit(page)
        self.lineEdit_Length.setValidator(HexValidator(99, self))
        self.lineEdit_Length.setFixedWidth(60)
        form.addRow("Length", self.lineEdit_Length)
        self.label_Length = form.labelForField(self.lineEdit_Length)

        self.spinBox_StartBit = QSpinBox(page)
        self.spinBox_StartBit.setRange(0, 7)
        form.addRow("Start bit", self.spinBox_StartBit)
        self.label_StartBit = form.labelForField(self.spinBox_StartBit)

        self.checkBox_ZeroTerminate = QCheckBox(page)
        form.addRow("Zero terminate", self.checkBox_ZeroTerminate)

        self.comboBox_Endianness = QComboBox(page)
        guiutils.fill_endianness_combobox(self.comboBox_Endianness)
        form.addRow("Endianness", self.comboBox_Endianness)
        self.label_Endianness = form.labelForField(self.comboBox_Endianness)

        self.checkBox_Hex = QCheckBox(page)
        self.checkBox_Signed = QCheckBox(page)
        repr_layout = QHBoxLayout()
        repr_layout.addWidget(self.checkBox_Hex)
        repr_layout.addWidget(self.checkBox_Signed)
        repr_layout.addStretch()
        form.addRow("Representation", repr_layout)

        self.comboBox_ValueType.currentIndexChanged.connect(self._alias_type_changed)
        self.checkBox_Hex.stateChanged.connect(self._repr_changed)
        self._alias_type_changed()
        return page

    def _build_enum_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)

        form = QFormLayout()
        self.comboBox_IntSize = QComboBox(page)
        for bits in (8, 16, 32, 64):
            self.comboBox_IntSize.addItem(f"Int{bits}", bits)
        form.addRow(tr.TYPE_UNDERLYING, self.comboBox_IntSize)
        self.checkBox_EnumSigned = QCheckBox(page)
        form.addRow("Signed", self.checkBox_EnumSigned)
        layout.addLayout(form)

        group = QGroupBox(tr.TYPE_ENUM_VALUES, page)
        group_layout = QVBoxLayout(group)
        self.tableWidget_Entries = QTableWidget(0, 2, group)
        self.tableWidget_Entries.setHorizontalHeaderLabels([tr.TYPE_ENUM_LABEL, tr.TYPE_ENUM_VALUE])
        self.tableWidget_Entries.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tableWidget_Entries.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        group_layout.addWidget(self.tableWidget_Entries)

        buttons = QHBoxLayout()
        self.pushButton_AddValue = QPushButton(tr.TYPE_ADD_VALUE, group)
        self.pushButton_RemoveValue = QPushButton(tr.TYPE_REMOVE_VALUE, group)
        buttons.addWidget(self.pushButton_AddValue)
        buttons.addWidget(self.pushButton_RemoveValue)
        buttons.addStretch()
        group_layout.addLayout(buttons)
        self.pushButton_AddValue.clicked.connect(lambda: self.tableWidget_Entries.insertRow(self.tableWidget_Entries.rowCount()))
        self.pushButton_RemoveValue.clicked.connect(self._remove_selected_entry)
        layout.addWidget(group)
        return page

    def _kind_changed(self) -> None:
        is_alias = self.comboBox_Kind.currentData() == typedefs.AliasType.kind
        self.stackedWidget.setCurrentIndex(0 if is_alias else 1)
        self.adjustSize()

    def _repr_changed(self) -> None:
        self.checkBox_Signed.setEnabled(not self.checkBox_Hex.isChecked())

    def _alias_type_changed(self) -> None:
        value_type = self.comboBox_ValueType.currentData()
        is_bit_field = isinstance(value_type, typedefs.BitFieldValueType)
        is_string = isinstance(value_type, typedefs.StringValueType)
        has_length = isinstance(value_type, (typedefs.StringValueType, typedefs.ByteArrayValueType)) or is_bit_field
        self.lineEdit_Length.setVisible(has_length)
        if self.label_Length is not None:
            self.label_Length.setVisible(has_length)
        self.spinBox_StartBit.setVisible(is_bit_field)
        if self.label_StartBit is not None:
            self.label_StartBit.setVisible(is_bit_field)
        self.checkBox_ZeroTerminate.setVisible(is_string)
        self.comboBox_Endianness.setVisible(not is_bit_field)
        if self.label_Endianness is not None:
            self.label_Endianness.setVisible(not is_bit_field)
        self.adjustSize()

    def _remove_selected_entry(self) -> None:
        row = self.tableWidget_Entries.currentRow()
        if row >= 0:
            self.tableWidget_Entries.removeRow(row)

    def _get_alias_value_type(self) -> typedefs.ValueType:
        value_type = self.comboBox_ValueType.currentData()
        length = utils.safe_str_to_int(self.lineEdit_Length.text(), 0)
        zero_terminate = self.checkBox_ZeroTerminate.isChecked()
        if self.checkBox_Hex.isChecked():
            value_repr = typedefs.VALUE_REPR.HEX
        elif self.checkBox_Signed.isChecked():
            value_repr = typedefs.VALUE_REPR.SIGNED
        else:
            value_repr = typedefs.VALUE_REPR.UNSIGNED
        if isinstance(value_type, typedefs.BitFieldValueType):
            return typedefs.BitFieldValueType(length, self.spinBox_StartBit.value(), value_repr=value_repr)
        return guiutils.configure_value_type(
            value_type,
            length=length,
            zero_terminate=zero_terminate,
            value_repr=value_repr,
            endian=self.comboBox_Endianness.currentData(),
        )

    def _get_enum_entries(self) -> list[tuple[str, int]] | None:
        entries = []
        labels = set()
        for row in range(self.tableWidget_Entries.rowCount()):
            label_item = self.tableWidget_Entries.item(row, 0)
            value_item = self.tableWidget_Entries.item(row, 1)
            label = label_item.text().strip() if label_item is not None else ""
            value_text = value_item.text().strip() if value_item is not None else ""
            if not label and not value_text:
                continue
            if not label:
                QMessageBox.warning(self, tr.ERROR, tr.TYPE_NAME_EMPTY)
                return None
            if label in labels:
                QMessageBox.warning(self, tr.ERROR, tr.TYPE_NAME_TAKEN)
                return None
            try:
                value = int(value_text, 0)
            except ValueError:
                QMessageBox.warning(self, tr.ERROR, tr.LENGTH_NOT_VALID)
                return None
            labels.add(label)
            entries.append((label, value))
        return entries

    def get_definition(self) -> typedefs.AliasType | typedefs.EnumType:
        name = self.lineEdit_Name.text().strip()
        if self.comboBox_Kind.currentData() == typedefs.AliasType.kind:
            return typedefs.AliasType(name, self._get_alias_value_type())
        integer_type = typedefs.IntegerValueType(
            self.comboBox_IntSize.currentData(),
            value_repr=typedefs.VALUE_REPR.SIGNED if self.checkBox_EnumSigned.isChecked() else typedefs.VALUE_REPR.UNSIGNED,
        )
        return typedefs.EnumType(name, integer_type, self._get_enum_entries())

    def _populate(self, definition: typedefs.AliasType | typedefs.EnumType) -> None:
        self.lineEdit_Name.setText(definition.name)
        if isinstance(definition, typedefs.AliasType):
            self.comboBox_Kind.setCurrentIndex(0)
            target = definition.value_type
            for index in range(self.comboBox_ValueType.count()):
                candidate = self.comboBox_ValueType.itemData(index)
                if (
                    type(candidate) is type(target)
                    and getattr(candidate, "bits", None) == getattr(target, "bits", None)
                    and getattr(candidate, "encoding", None) == getattr(target, "encoding", None)
                ):
                    self.comboBox_ValueType.setCurrentIndex(index)
                    break
            if isinstance(target, (typedefs.StringValueType, typedefs.ByteArrayValueType)):
                self.lineEdit_Length.setText(str(target.length))
            elif isinstance(target, typedefs.BitFieldValueType):
                self.lineEdit_Length.setText(str(target.bits))
                self.spinBox_StartBit.setValue(target.start_bit)
            if isinstance(target, typedefs.StringValueType):
                self.checkBox_ZeroTerminate.setChecked(target.zero_terminate)
            value_repr = getattr(target, "value_repr", typedefs.VALUE_REPR.UNSIGNED)
            self.checkBox_Hex.setChecked(value_repr == typedefs.VALUE_REPR.HEX)
            self.checkBox_Signed.setChecked(value_repr == typedefs.VALUE_REPR.SIGNED)
            self.checkBox_Signed.setEnabled(value_repr != typedefs.VALUE_REPR.HEX)
            endian_index = self.comboBox_Endianness.findData(getattr(target, "endian", typedefs.ENDIANNESS.HOST))
            if endian_index >= 0:
                self.comboBox_Endianness.setCurrentIndex(endian_index)
        else:
            self.comboBox_Kind.setCurrentIndex(1)
            size_index = self.comboBox_IntSize.findData(definition.integer_type.bits)
            if size_index >= 0:
                self.comboBox_IntSize.setCurrentIndex(size_index)
            self.checkBox_EnumSigned.setChecked(definition.integer_type.value_repr == typedefs.VALUE_REPR.SIGNED)
            self.tableWidget_Entries.setRowCount(0)
            for label, value in definition.entries:
                row = self.tableWidget_Entries.rowCount()
                self.tableWidget_Entries.insertRow(row)
                self.tableWidget_Entries.setItem(row, 0, QTableWidgetItem(label))
                self.tableWidget_Entries.setItem(row, 1, QTableWidgetItem(str(value)))
        self._kind_changed()

    def accept(self) -> None:
        if not self.lineEdit_Name.text().strip():
            QMessageBox.warning(self, tr.ERROR, tr.TYPE_NAME_EMPTY)
            return
        if self.comboBox_Kind.currentData() == typedefs.EnumType.kind and self._get_enum_entries() is None:
            return
        super().accept()
