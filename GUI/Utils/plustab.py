from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QTabWidget, QWidget


class PlusTabManager(QObject):
    """Adds a trailing "+" pseudo-tab to a QTabWidget and keeps it pinned as the last tab

    The manager owns only the "+" tab mechanics. Hosts keep their own logic for creating,
    closing and renaming real tabs by connecting to the signals below.
    """

    add_requested = pyqtSignal()
    close_requested = pyqtSignal(int)
    rename_requested = pyqtSignal(int)

    def __init__(self, tab_widget: QTabWidget, tooltip: str | None = None, label: str = "+") -> None:
        """
        Args:
            tab_widget (QTabWidget): Tab widget the "+" tab will be added to
            tooltip (str | None): Tooltip of the "+" tab, if any
            label (str): Label of the "+" tab
        """
        super().__init__(tab_widget)
        self.tab_widget = tab_widget
        self.plus_widget = QWidget()
        self.tab_widget.addTab(self.plus_widget, label)
        self._strip_close_button()
        if tooltip is not None:
            self.set_tooltip(tooltip)
        self._reentrant_move = False
        tab_bar = self.tab_widget.tabBar()
        self.tab_widget.tabBarClicked.connect(self._on_tab_bar_clicked)
        self.tab_widget.tabCloseRequested.connect(self._on_tab_close_requested)
        self.tab_widget.tabBarDoubleClicked.connect(self._on_tab_bar_double_clicked)
        tab_bar.tabMoved.connect(self._on_tab_moved)

    def _strip_close_button(self) -> None:
        index = self.plus_index()
        tab_bar = self.tab_widget.tabBar()
        tab_bar.setTabButton(index, tab_bar.ButtonPosition.LeftSide, None)
        tab_bar.setTabButton(index, tab_bar.ButtonPosition.RightSide, None)

    def plus_index(self) -> int:
        return self.tab_widget.indexOf(self.plus_widget)

    def is_plus(self, index: int) -> bool:
        return self.tab_widget.widget(index) is self.plus_widget

    def real_count(self) -> int:
        return self.tab_widget.count() - 1

    def set_tooltip(self, tooltip: str) -> None:
        self.tab_widget.setTabToolTip(self.plus_index(), tooltip)

    def insert_before_plus(self, widget: QWidget, title: str) -> int:
        index = self.plus_index()
        self.tab_widget.insertTab(index, widget, title)
        return index

    def _on_tab_bar_clicked(self, index: int) -> None:
        if self.is_plus(index):
            self.add_requested.emit()

    def _on_tab_close_requested(self, index: int) -> None:
        if not self.is_plus(index):
            self.close_requested.emit(index)

    def _on_tab_bar_double_clicked(self, index: int) -> None:
        if not self.is_plus(index):
            self.rename_requested.emit(index)

    def _on_tab_moved(self, from_index: int, to_index: int) -> None:
        if self._reentrant_move:
            return
        plus_index = self.plus_index()
        if plus_index == self.tab_widget.count() - 1:
            return
        self._reentrant_move = True
        try:
            self.tab_widget.tabBar().moveTab(plus_index, self.tab_widget.count() - 1)
        finally:
            self._reentrant_move = False
