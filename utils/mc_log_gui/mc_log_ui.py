#!/usr/bin/env python
# -*- coding: utf-8 -*-

import collections
import copy
import csv
import ctypes
import json
import numpy as np
import os
import re
import signal
import sys
import tempfile

from functools import partial

from PySide import QtCore, QtGui

from mc_log_main_ui import Ui_MainWindow
from mc_log_tab import MCLogTab
from mc_log_types import LineStyle

try:
  import mc_rbdyn
except ImportError:
  mc_rbdyn = None

UserPlot = collections.namedtuple('UserPlot', ['title', 'x', 'y1', 'y1d', 'y2', 'y2d', 'grid1', 'grid2', 'style', 'style2'])

def safe_float(v):
    if len(v):
        return float(v)
    else:
        return None

def read_flat(f):
    def read_size(fd):
        return ctypes.c_size_t.from_buffer_copy(fd.read(ctypes.sizeof(ctypes.c_size_t))).value
    def read_bool(fd):
        return ctypes.c_bool.from_buffer_copy(fd.read(ctypes.sizeof(ctypes.c_bool))).value
    def read_string(fd, size):
        if size == 0:
            return None
        return fd.read(size).decode('ascii')
    def read_array(fd, size):
        return np.frombuffer(fd.read(size * ctypes.sizeof(ctypes.c_double)), np.double)
    def read_string_array(fd, size):
        return [ read_string(fd, read_size(fd)) for i in range(size) ]
    data = {}
    with open(f, 'rb') as fd:
        nrEntries = read_size(fd)
        for i in range(nrEntries):
            is_numeric = read_bool(fd)
            key = read_string(fd, read_size(fd))
            if is_numeric:
                data[key] = read_array(fd, read_size(fd))
            else:
                entries = read_string_array(fd, read_size(fd))
                entries_to_int = {None: None}
                i = 0
                for v in entries:
                    if v in entries_to_int:
                        continue
                    entries_to_int[v] = i
                    i += 1
                data[key] = [ entries_to_int[v] for v in entries ]
    return data

class CommonStyleDialog(QtGui.QDialog):
  def __init__(self, parent, name, canvas, style):
    super(CommonStyleDialog, self).__init__(parent)

    self.name = name
    self.canvas = canvas
    self.style = style

    self.setWindowTitle('Edit {} grid style'.format(name))
    self.setModal(True)

    self.layout = QtGui.QFormLayout(self)

    self.linestyle = QtGui.QComboBox()
    styles = ['-', ':', '--', '-.']
    for s in styles:
      self.linestyle.addItem(s)
    self.linestyle.setCurrentIndex(styles.index(style.linestyle))
    self.layout.addRow("Style", self.linestyle)

    self.linewidth = QtGui.QLineEdit(str(style.linewidth))
    self.linewidth.setValidator(QtGui.QDoubleValidator(0.01, 1e6, 2))
    self.layout.addRow("Width", self.linewidth)

    self.color = QtGui.QColor(style.color)
    self.colorButton = QtGui.QPushButton("#")
    self.colorButton.setStyleSheet("background-color: {}; color: {}".format(self.style.color, self.style.color))
    self.colorButton.released.connect(self.selectColor)
    self.layout.addRow("Color", self.colorButton)

    confirmLayout = QtGui.QHBoxLayout()
    okButton = QtGui.QPushButton("Ok", self)
    confirmLayout.addWidget(okButton)
    okButton.clicked.connect(self.accept)
    cancelButton = QtGui.QPushButton("Cancel", self)
    confirmLayout.addWidget(cancelButton)
    cancelButton.clicked.connect(self.reject)
    self.layout.addRow(confirmLayout)

  def selectColor(self):
    color = QtGui.QColorDialog.getColor(self.color)
    if color.isValid():
      self.color = color
      self.colorButton.setStyleSheet("background-color: {}; color: {}".format(self.color.name(), self.color.name()))

  def accept(self):
    super(CommonStyleDialog, self).accept()
    self.style.linestyle = self.linestyle.currentText()
    self.style.linewidth = float(self.linewidth.text())
    self.style.color = self.color.name()

class GridStyleDialog(CommonStyleDialog):
  def __init__(self, parent, name, canvas, style):
    super(GridStyleDialog, self).__init__(parent, name, canvas, style)

    self.enabled = QtGui.QCheckBox()
    self.enabled.setChecked(style.visible)
    self.layout.insertRow(0, "Visible", self.enabled)

    self.save = QtGui.QCheckBox()
    self.layout.insertRow(self.layout.rowCount() - 2, "Save as default", self.save)

  def accept(self):
    super(GridStyleDialog, self).accept()
    self.style.visible = self.enabled.isChecked()
    self.canvas.draw()
    if self.save.isChecked():
      self.parent().gridStyles[self.name] = self.style
      with open(self.parent().gridStyleFile, 'w') as f:
        json.dump(self.parent().gridStyles, f, default = lambda o: o.__dict__)

class LineStyleDialog(CommonStyleDialog):
  def __init__(self, parent, name, canvas, style, set_style_fn):
    super(LineStyleDialog, self).__init__(parent, name, canvas, style)
    self.set_style = set_style_fn

  def accept(self):
    super(LineStyleDialog, self).accept()
    self.set_style(self.name, self.style)
    self.canvas.draw()

class MCLogJointDialog(QtGui.QDialog):
  def __init__(self, parent, rm, name, y1_prefix = None, y2_prefix = None, y1_diff_prefix = None, y2_diff_prefix = None):
    super(MCLogJointDialog, self).__init__(parent)
    self.setWindowTitle("Select plot joints")
    self.setModal(True)
    self.joints = []
    self.name = name
    self.y1_prefix = y1_prefix
    self.y2_prefix = y2_prefix
    self.y1_diff_prefix = y1_diff_prefix
    self.y2_diff_prefix = y2_diff_prefix
    layout = QtGui.QVBoxLayout(self)

    jointsBox = QtGui.QGroupBox("Joints", self)
    grid = QtGui.QGridLayout(jointsBox)
    margins = grid.contentsMargins()
    margins.setTop(20)
    grid.setContentsMargins(margins)
    self.jointsCBox = []
    row = 0
    col = 0
    if rm is not None:
        for i, j in enumerate(rm.ref_joint_order()):
          cBox = QtGui.QCheckBox(j, self)
          cBox.stateChanged.connect(partial(self.checkboxChanged, j))
          grid.addWidget(cBox, row, col)
          self.jointsCBox.append(cBox)
          col += 1
          if col == 4:
            col = 0
            row += 1
    layout.addWidget(jointsBox)

    optionsBox = QtGui.QGroupBox("Options", self)
    optionsLayout = QtGui.QHBoxLayout(optionsBox)
    margins = optionsLayout.contentsMargins()
    margins.setTop(20)
    optionsLayout.setContentsMargins(margins)
    self.selectAllBox = QtGui.QCheckBox("Select all", self)
    self.selectAllBox.stateChanged.connect(self.selectAllBoxChanged)
    optionsLayout.addWidget(self.selectAllBox)
    self.onePlotPerJointBox = QtGui.QCheckBox("One plot per joint", self)
    optionsLayout.addWidget(self.onePlotPerJointBox)
    self.plotLimits = QtGui.QCheckBox("Plot limits", self)
    optionsLayout.addWidget(self.plotLimits)
    layout.addWidget(optionsBox)

    confirmLayout = QtGui.QHBoxLayout()
    okButton = QtGui.QPushButton("Ok", self)
    confirmLayout.addWidget(okButton)
    okButton.clicked.connect(self.okButton)
    cancelButton = QtGui.QPushButton("Cancel", self)
    confirmLayout.addWidget(cancelButton)
    cancelButton.clicked.connect(self.reject)
    layout.addLayout(confirmLayout)

  def okButton(self):
    if len(self.joints):
      plotLimits = self.plotLimits.isChecked()
      if self.onePlotPerJointBox.isChecked():
        [ self.parent().plot_joint_data(self.name + ": {}".format(j), [j], self.y1_prefix, self.y2_prefix, self.y1_diff_prefix, self.y2_diff_prefix, plotLimits) for j in self.joints ]
      else:
        self.parent().plot_joint_data(self.name, self.joints, self.y1_prefix, self.y2_prefix, self.y1_diff_prefix, self.y2_diff_prefix, plotLimits)
    self.accept()

  def checkboxChanged(self, item, state):
    if state:
      self.joints.append(item)
    else:
      self.joints.remove(item)

  def selectAllBoxChanged(self, state):
    for cBox in self.jointsCBox:
      cBox.setChecked(state)
    if state:
      self.selectAllBox.setText("Select none")
    else:
      self.selectAllBox.setText("Select all")

class DumpSeqPlayDialog(QtGui.QDialog):
  def __init__(self, parent):
    super(DumpSeqPlayDialog, self).__init__(parent)
    self.setWindowTitle("Dump qIn to seqplay")
    self.setModal(True)

    layout = QtGui.QGridLayout(self)
    row = 0

    row += 1
    layout.addWidget(QtGui.QLabel("Timestep"), row, 0)
    self.timestepLineEdit = QtGui.QLineEdit("0.005")
    validator = QtGui.QDoubleValidator()
    validator.setBottom(1e-6)
    self.timestepLineEdit.setValidator(validator)
    layout.addWidget(self.timestepLineEdit, row, 1)

    row += 1
    layout.addWidget(QtGui.QLabel("Time scale"), row, 0)
    self.timeScaleSpinBox = QtGui.QSpinBox()
    self.timeScaleSpinBox.setMinimum(1)
    self.timeScaleSpinBox.setPrefix("x")
    layout.addWidget(self.timeScaleSpinBox, row, 1)

    row += 1
    filedialogButton = QtGui.QPushButton("Browse...")
    filedialogButton.clicked.connect(self.filedialogButton)
    layout.addWidget(filedialogButton, row, 0)
    self.fileLineEdit = QtGui.QLineEdit("out.pos")
    layout.addWidget(self.fileLineEdit)

    row += 1
    okButton = QtGui.QPushButton("Ok", self)
    okButton.clicked.connect(self.okButton)
    layout.addWidget(okButton, row, 0)
    cancelButton = QtGui.QPushButton("Cancel", self)
    cancelButton.clicked.connect(self.reject)
    layout.addWidget(cancelButton, row, 1)

  def okButton(self):
    fout = self.fileLineEdit.text()
    tScale = self.timeScaleSpinBox.value()
    dt = float(self.timestepLineEdit.text())
    rm = self.parent().rm
    data = self.parent().data
    if os.path.exists(fout):
      overwrite = QtGui.QMessageBox.question(self, "Overwrite existing file", "{} already exists, do you want to overwrite it?".format(fout), QtGui.QMessageBox.Yes, QtGui.QMessageBox.No)
      if overwrite == QtGui.QMessageBox.No:
        return
    with open(fout, 'w') as fd:
      rjo_range = range(len(rm.ref_joint_order()))
      i_range = range(len(data['t']))
      t = 0
      for i in i_range:
        q = np.array([data['qIn_{}'.format(jIdx)][i] for jIdx in rjo_range])
        if i == i_range[-1]:
          next_q = q
        else:
          next_q = np.array([data['qIn_{}'.format(jIdx)][i+1] for jIdx in rjo_range])
        for j in range(tScale):
          qOut = map(str, q + j/float(tScale) * (next_q - q))
          fd.write("{} {}\n".format(t, " ".join(qOut)))
          t += dt
    self.accept()

  def filedialogButton(self):
    fpath = QtGui.QFileDialog.getSaveFileName(self, "Output file")[0]
    if len(fpath):
      self.fileLineEdit.setText(fpath)

class MCLogUI(QtGui.QMainWindow):
  def __init__(self, parent = None):
    super(MCLogUI, self).__init__(parent)
    self.__init__ui = Ui_MainWindow()
    self.ui = Ui_MainWindow()

    self.ui.setupUi(self)

    self.tab_re = re.compile('^Plot [0-9]+$')

    self.data = {}

    self.gridStyles = {'left': LineStyle(), 'right': LineStyle(linestyle = ':') }
    self.gridStyleFile = os.path.expanduser("~") + "/.config/mc_log_ui/grid_style.json"
    if os.path.exists(self.gridStyleFile):
      with open(self.gridStyleFile) as f:
        data = json.load(f)
        for k in self.gridStyles.keys():
          if k in data:
            self.gridStyles[k] = LineStyle(**data[k])
    UserPlot.__new__.__defaults__ = (self.gridStyles['left'], self.gridStyles['right'], {}, {})

    self.userPlotList = []
    self.userPlotFile = os.path.expanduser("~") + "/.config/mc_log_ui/custom_plot.json"
    if os.path.exists(self.userPlotFile):
      with open(self.userPlotFile) as f:
        self.userPlotList = [UserPlot(*x) for x in json.load(f)]
        for plt in self.userPlotList:
          for y in plt.style:
            plt.style[y] = LineStyle(**plt.style[y])
          for y in plt.style2:
            plt.style2[y] = LineStyle(**plt.style2[y])
    self.update_userplot_menu()

    self.activeRobotAction = None
    self.rm = None
    if mc_rbdyn is not None:
      rMenu = QtGui.QMenu("Robot", self.ui.menubar)
      rGroup = QtGui.QActionGroup(rMenu)
      for r in mc_rbdyn.RobotLoader.available_robots():
        rAct = QtGui.QAction(r, rGroup)
        rAct.setCheckable(True)
        rGroup.addAction(rAct)
      rMenu.addActions(rGroup.actions())
      rGroup.actions()[0].setChecked(True)
      self.activeRobotAction = rGroup.actions()[0]
      self.setRobot(rGroup.actions()[0])
      self.connect(rGroup, QtCore.SIGNAL("triggered(QAction *)"), self.setRobot)
      self.ui.menubar.addMenu(rMenu)

    self.styleMenu = QtGui.QMenu("Style", self.ui.menubar)

    # Line style menu
    self.lineStyleMenu = QtGui.QMenu("Graph", self.styleMenu)
    def fillLineStyleMenu(self):
      self.lineStyleMenu.clear()
      canvas = self.getCanvas()
      def makePlotMenu(self, name, plots, style_fn):
        if not len(plots):
          return
        menu = QtGui.QMenu(name, self.lineStyleMenu)
        group = QtGui.QActionGroup(act)
        for y in plots:
          action = QtGui.QAction(y, group)
          action.triggered.connect(lambda yin=y: LineStyleDialog(self, yin, self.getCanvas(), style_fn(yin), style_fn).exec_())
          group.addAction(action)
        menu.addActions(group.actions())
        self.lineStyleMenu.addMenu(menu)
      makePlotMenu(self, "Left", canvas.axes_plots.keys(), canvas.style_left)
      makePlotMenu(self, "Right", canvas.axes2_plots.keys(), canvas.style_right)
    self.lineStyleMenu.aboutToShow.connect(lambda: fillLineStyleMenu(self))
    self.styleMenu.addMenu(self.lineStyleMenu)

    # Grid style menu
    self.gridStyleMenu = QtGui.QMenu("Grid", self.styleMenu)
    self.gridDisplayActionGroup = QtGui.QActionGroup(self.gridStyleMenu)
    self.gridDisplayActionGroup.setExclusive(True)
    self.leftGridAction = QtGui.QAction("Left", self.gridDisplayActionGroup)
    self.leftGridAction.triggered.connect(lambda: GridStyleDialog(self, "left", self.getCanvas(), self.getCanvas().grid).exec_())
    self.gridDisplayActionGroup.addAction(self.leftGridAction)
    self.rightGridAction = QtGui.QAction("Right", self.gridDisplayActionGroup)
    self.rightGridAction.triggered.connect(lambda: GridStyleDialog(self, "right", self.getCanvas(), self.getCanvas().grid2).exec_())
    self.gridDisplayActionGroup.addAction(self.rightGridAction)
    self.gridStyleMenu.addActions(self.gridDisplayActionGroup.actions())
    self.styleMenu.addMenu(self.gridStyleMenu)

    self.ui.menubar.addMenu(self.styleMenu)

    self.toolsMenu = QtGui.QMenu("Tools", self.ui.menubar)
    act = QtGui.QAction("Dump qIn to seqplay", self.toolsMenu)
    act.triggered.connect(DumpSeqPlayDialog(self).exec_)
    self.toolsMenu.addAction(act)
    self.ui.menubar.addMenu(self.toolsMenu)

    self.addApplicationShortcut(QtCore.Qt.CTRL + QtCore.Qt.Key_O, self.shortcutOpenFile)
    self.addApplicationShortcut(QtCore.Qt.CTRL + QtCore.Qt.Key_W, self.shortcutCloseTab)
    self.addApplicationShortcut(QtCore.Qt.CTRL + QtCore.Qt.Key_PageDown, self.shortcutNextTab)
    self.addApplicationShortcut(QtCore.Qt.CTRL + QtCore.Qt.Key_PageUp, self.shortcutPreviousTab)
    self.addApplicationShortcut(QtCore.Qt.CTRL + QtCore.Qt.Key_T, self.shortcutNewTab)

  def saveUserPlots(self):
    confDir = os.path.dirname(self.userPlotFile)
    if not os.path.exists(confDir):
      os.makedirs(confDir)
    with open(self.userPlotFile, 'w') as f:
        json.dump(self.userPlotList, f, default = lambda o: o.__dict__, indent = 2, separators = (',', ': '))
    self.update_userplot_menu()

  def addApplicationShortcut(self, key, callback):
    shortcut = QtGui.QShortcut(self)
    shortcut.setKey(key)
    shortcut.setContext(QtCore.Qt.ShortcutContext.ApplicationShortcut)
    shortcut.activated.connect(lambda: callback())

  def update_userplot_menu(self):
    self.ui.menuUserPlots.clear()
    for p in self.userPlotList:
      act = QtGui.QAction(p.title, self.ui.menuUserPlots)
      act.triggered.connect(lambda plot=p: self.plot_userplot(plot))
      self.ui.menuUserPlots.addAction(act)
    act = QtGui.QAction("Save current plot", self.ui.menuUserPlots)
    act.triggered.connect(self.save_userplot)
    self.ui.menuUserPlots.addAction(act)
    if len(self.userPlotList):
      rmUserPlotMenu = QtGui.QMenu("Remove saved plots", self.ui.menuUserPlots)
      for p in self.userPlotList:
        act = QtGui.QAction(p.title, self.ui.menuUserPlots)
        act.triggered.connect(lambda plot=p: self.remove_userplot(plot))
        rmUserPlotMenu.addAction(act)
      self.ui.menuUserPlots.addMenu(rmUserPlotMenu)

  def save_userplot(self):
    tab = self.ui.tabWidget.currentWidget()
    canvas = tab.ui.canvas
    valid = len(canvas.axes_plots) != 0 or len(cavas.axes2_plots) != 0
    if not valid:
      err_diag = QtGui.QMessageBox(self)
      err_diag.setModal(True)
      err_diag.setText("Cannot save user plot if nothing is shown")
      err_diag.exec_()
      return
    defaultTitle = self.ui.tabWidget.tabText(self.ui.tabWidget.currentIndex())
    if defaultTitle.startswith("Plot"):
      defaultTitle = ""
    title, ok = QtGui.QInputDialog.getText(self, "User plot", "Title of your plot:", text = defaultTitle)
    if ok:
      y1 = filter(lambda k: k in self.data.keys(), canvas.axes_plots.keys())
      y2 = filter(lambda k: k in self.data.keys(), canvas.axes2_plots.keys())
      y1d = map(lambda sp: "{}_{}".format(sp.name, sp.id), filter(lambda sp: sp.idx == 0, tab.specials.values()))
      y2d = map(lambda sp: "{}_{}".format(sp.name, sp.id), filter(lambda sp: sp.idx == 1, tab.specials.values()))
      style = { y: canvas.style_left(y) for y in canvas.axes_plots.keys() }
      style2 = { y: canvas.style_right(y) for y in canvas.axes2_plots.keys() }
      found = False
      for i in range(len(self.userPlotList)):
        if self.userPlotList[i].title == title:
          self.userPlotList[i] = UserPlot(title, tab.x_data, y1, y1d, y2, y2d, self.getCanvas().grid, self.getCanvas().grid2, style, style2)
          found = True
          break
      if not found:
        self.userPlotList.append(UserPlot(title, tab.x_data, y1, y1d, y2, y2d, self.getCanvas().grid, self.getCanvas().grid2, style, style2) )
      self.saveUserPlots()

  def plot_userplot(self, p):
    valid = p.x in self.data.keys() and all([y in self.data.keys() for x in [p.y1, p.y2] for y in x])
    if not valid:
      missing_entries = ""
      if not p.x in self.data.keys():
        missing_entries += "- {}\n".format(p.x)
      for x in [p.y1, p.y1d, p.y2, p.y2d]:
        for y in x:
          if not y in self.data.keys():
            missing_entries += "- {}\n".format(y)
      missing_entries = missing_entries[:-1]
      err_diag = QtGui.QMessageBox(self)
      err_diag.setModal(True)
      err_diag.setText("Plot {} is not valid for this log file, some data is missing\nMissing entries:\n{}".format(p.title, missing_entries))
      err_diag.exec_()
      return
    plotW = MCLogTab.UserPlot(self, p)
    self.ui.tabWidget.insertTab(self.ui.tabWidget.count() - 1, plotW, p.title)
    self.ui.tabWidget.setCurrentIndex(self.ui.tabWidget.count() - 2)
    self.updateClosable()

  def remove_userplot(self, p_in):
    for p in self.userPlotList:
      if p.title == p_in.title:
        self.userPlotList.remove(p)
        break
    self.saveUserPlots()

  def setRobot(self, action):
    try:
      self.rm = mc_rbdyn.RobotLoader.get_robot_module(action.text())
      self.activeRobotAction = action
      for i in range(self.ui.tabWidget.count() - 1):
        tab = self.ui.tabWidget.widget(i)
        assert(isinstance(tab, MCLogTab))
        tab.setRobotModule(self.rm)
    except RuntimeError:
      #QtGui.QMessageBox.warning(self, "Failed to get RobotModule", "Could not retrieve Robot Module: {}{}Check your console for more details".format(action.text(), os.linesep))
      action.setChecked(False)
      self.activeRobotAction.setChecked(True)
      self.rm = None

  def getCanvas(self):
    return self.ui.tabWidget.currentWidget().ui.canvas

  @QtCore.Slot()
  def on_actionLoad_triggered(self):
    fpath = QtGui.QFileDialog.getOpenFileName(self, "Log file")[0]
    if len(fpath):
      self.load_csv(fpath)

  @QtCore.Slot()
  def on_actionExit_triggered(self):
    QtGui.QApplication.quit()

  @QtCore.Slot(int)
  def on_tabWidget_currentChanged(self, idx):
    if idx == self.ui.tabWidget.count() - 1:
      plotW = MCLogTab(self)
      plotW.setData(self.data)
      plotW.setGridStyles(self.gridStyles)
      plotW.setRobotModule(self.rm)
      j = 1
      for i in range(self.ui.tabWidget.count() -1):
        if self.tab_re.match(self.ui.tabWidget.tabText(i)):
          j += 1
      self.ui.tabWidget.insertTab(self.ui.tabWidget.count() - 1, plotW, "Plot {}".format(j))
      self.ui.tabWidget.setCurrentIndex(self.ui.tabWidget.count() - 2)
      self.updateClosable()

  @QtCore.Slot(int)
  def on_tabWidget_tabCloseRequested(self, idx):
    self.ui.tabWidget.setCurrentIndex(abs(idx - 1))
    self.ui.tabWidget.removeTab(idx)
    j = 1
    for i in range(self.ui.tabWidget.count() - 1):
      if self.tab_re.match(self.ui.tabWidget.tabText(i)):
        self.ui.tabWidget.setTabText(i, "Plot {}".format(j))
        j += 1
    self.updateClosable()

  def updateClosable(self):
    has_closable = self.ui.tabWidget.count() > 2
    self.ui.tabWidget.setTabsClosable(has_closable)
    if has_closable:
      self.ui.tabWidget.tabBar().tabButton(self.ui.tabWidget.count() - 1, QtGui.QTabBar.RightSide).hide();

  def shortcutOpenFile(self):
    self.ui.actionLoad.triggered.emit()

  def shortcutCloseTab(self):
    if self.ui.tabWidget.tabsClosable():
      self.ui.tabWidget.tabCloseRequested.emit(self.ui.tabWidget.currentIndex())

  def shortcutPreviousTab(self):
    if self.ui.tabWidget.currentIndex() > 0:
      self.ui.tabWidget.setCurrentIndex(self.ui.tabWidget.currentIndex() - 1)

  def shortcutNextTab(self):
    if self.ui.tabWidget.currentIndex() < self.ui.tabWidget.count() - 2:
      self.ui.tabWidget.setCurrentIndex(self.ui.tabWidget.currentIndex() + 1)

  def shortcutNewTab(self):
    self.ui.tabWidget.setCurrentIndex(self.ui.tabWidget.count() - 1)

  def load_csv(self, fpath, tmp = False):
    self.data = {}
    if fpath.endswith('.bin'):
      tmpf = tempfile.mkstemp(suffix = '.flat')[1]
      os.system("mc_bin_to_flat {} {}".format(fpath, tmpf))
      return self.load_csv(tmpf, True)
    elif fpath.endswith('.flat'):
      self.data = read_flat(fpath)
    else:
      with open(fpath) as fd:
        reader = csv.DictReader(fd, delimiter=';')
        for k in reader.fieldnames:
          self.data[k] = []
        for row in reader:
          for k in reader.fieldnames:
            self.data[k].append(safe_float(row[k]))
      for k in self.data:
        self.data[k] = np.array(self.data[k])
    i = 0
    while "qIn_{}".format(i) in self.data and "qOut_{}".format(i) in self.data:
      self.data["error_{}".format(i)] = self.data["qOut_{}".format(i)] - self.data["qIn_{}".format(i)]
      self.data["qIn_limits_lower_{}".format(i)] = np.full_like(self.data["qIn_{}".format(i)], 0)
      self.data["qIn_limits_upper_{}".format(i)] = np.full_like(self.data["qIn_{}".format(i)], 0)
      self.data["qOut_limits_lower_{}".format(i)] = self.data["qIn_limits_lower_{}".format(i)]
      self.data["qOut_limits_upper_{}".format(i)] = self.data["qIn_limits_upper_{}".format(i)]
      i += 1
    i = 0
    while "tauIn_{}".format(i) in self.data:
      self.data["tauIn_limits_lower_{}".format(i)] = np.full_like(self.data["tauIn_{}".format(i)], 0)
      self.data["tauIn_limits_upper_{}".format(i)] = np.full_like(self.data["tauIn_{}".format(i)], 0)
      i += 1
    self.update_data()
    if tmp:
      os.unlink(fpath)

  def update_data(self):
    self.update_menu()
    for i in range(self.ui.tabWidget.count() - 1):
      tab = self.ui.tabWidget.widget(i)
      assert(isinstance(tab, MCLogTab))
      tab.setData(self.data)
      tab.setGridStyles(self.gridStyles)
      tab.setRobotModule(self.rm)

  def update_menu(self):
    self.ui.menuCommonPlots.clear()
    menuEntries = [
        ("Encoders", "qIn", None, None, None),
        ("Commands", "qOut", None, None, None),
        ("Error", "error", None, None, None),
        ("Torques", "tauIn", None, None, None),
        ("Encoders/Commands", "qIn", "qOut", None, None),
        ("Error/Torque", "error", "tauIn", None, None),
        ("Encoders velocity", None, None, "qIn", None),
        ("Command velocity", None, None, "qOut", None),
        ("Encoders/Commands velocity", None, None, "qIn", "qOut"),
        ("Encoders/Encoders velocity", "qIn", None, None, "qIn"),
        ("Command/Command velocity", "qOut", None, None, "qOut"),
        ]
    def validEntry(y):
      return any([ y is None or (y is not None and k.startswith(y)) for k in self.data.keys() ])
    menuEntries = [ (n, y1, y2, y1d, y2d) for n, y1, y2, y1d, y2d in menuEntries if all([validEntry(y) for y in [y1, y2, y1d, y2d]]) ]
    for n, y1, y2, y1d, y2d in menuEntries:
      act = QtGui.QAction(n, self.ui.menuCommonPlots)
      act.triggered.connect(lambda: MCLogJointDialog(self, self.rm, n, y1, y2, y1d, y2d).exec_())
      self.ui.menuCommonPlots.addAction(act)
    fSensors = set()
    for k in self.data:
      if k.find('ForceSensor') != -1:
        fSensors.add(k[:k.find('ForceSensor')])
    if len(fSensors):
      fsMenu = QtGui.QMenu("Force sensors", self.ui.menuCommonPlots)
      for f in sorted(fSensors):
        act = QtGui.QAction(f, fsMenu)
        act.triggered.connect(partial(self.plot_force_sensor, f))
        fsMenu.addAction(act)
      self.ui.menuCommonPlots.addMenu(fsMenu)

  def plot_force_sensor(self, fs):
    plotW = MCLogTab.ForceSensorPlot(self, fs)
    self.ui.tabWidget.insertTab(self.ui.tabWidget.count() - 1, plotW, "FS: {}".format(fs))
    self.ui.tabWidget.setCurrentIndex(self.ui.tabWidget.count() - 2)
    self.updateClosable()

  def plot_joint_data(self, name, joints, y1_prefix = None, y2_prefix = None,
                                    y1_diff_prefix = None, y2_diff_prefix = None, plot_limits = False):
    plotW = MCLogTab.JointPlot(self, joints, y1_prefix, y2_prefix, y1_diff_prefix, y2_diff_prefix, plot_limits)
    self.ui.tabWidget.insertTab(self.ui.tabWidget.count() - 1, plotW, name)
    self.ui.tabWidget.setCurrentIndex(self.ui.tabWidget.count() - 2)
    self.updateClosable()

if __name__ == '__main__':
  app = QtGui.QApplication(sys.argv)
  gui = MCLogUI()
  if len(sys.argv) > 1:
    gui.load_csv(sys.argv[1])
  gui.showMaximized()

  def sigint_handler(*args):
    gui.close()
  signal.signal(signal.SIGINT, sigint_handler)

  sys.exit(app.exec_())
