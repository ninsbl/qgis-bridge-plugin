import os
import traceback
import requests

from qgis.PyQt import uic
from qgis.PyQt.QtCore import (
    Qt,
    QSize,
    QCoreApplication
)
from qgis.PyQt.QtWidgets import (
    QProgressBar,
    QLabel,
    QMenu,
    QWidget,
    QComboBox,
    QHBoxLayout,
    QSizePolicy,
    QCheckBox,
    QListWidgetItem,
    QTableWidgetItem,
    QMessageBox,
    QFileDialog
) 
from qgis.PyQt.QtGui import (
    QIcon,
    QPixmap
)

from qgis.core import (
    QgsCoordinateTransform,
    QgsMessageOutput,
    QgsMapLayer,
    QgsMessageLog,
    QgsNativeMetadataValidator, 
    QgsLayerTreeGroup,
    QgsLayerTreeLayer,
    QgsMapLayer,
    QgsProject,
    Qgis,
    QgsApplication,
    QgsRectangle
)
from qgis.gui import QgsMessageBar, QgsMetadataWidget
from qgis.utils import iface

from geocatbridge.utils.gui import execute
from geocatbridge.publish.geonetwork import GeonetworkServer
from geocatbridge.publish.publishtask import PublishTask, ExportTask
from geocatbridge.publish.servers import geodataServers, metadataServers
from geocatbridge.publish.metadata import uuidForLayer, loadMetadataFromXml
from geocatbridge.ui.metadatadialog import MetadataDialog
from geocatbridge.ui.publishreportdialog import PublishReportDialog
from geocatbridge.ui.progressdialog import ProgressDialog

def iconPath(icon):
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "icons", icon)

PUBLISHED_ICON = QIcon(iconPath("published.png"))
ERROR_ICON = '<img src="%s">' % iconPath("error-red.png")
REMOVE_ICON = QIcon(iconPath("remove.png"))
VALIDATE_ICON = QIcon(iconPath("validation.png"))
PREVIEW_ICON = QIcon(iconPath("preview.png"))
IMPORT_ICON = QIcon(iconPath("save.png"))

IDENTIFICATION, CATEGORIES, KEYWORDS, ACCESS, EXTENT, CONTACT = range(6)

WIDGET, BASE = uic.loadUiType(os.path.join(os.path.dirname(__file__), 'publishwidget.ui'))

class PublishWidget(BASE, WIDGET):

    def __init__(self, parent):
        super(PublishWidget, self).__init__()
        self.isMetadataPublished = {}
        self.isDataPublished = {}
        self.currentRow = None
        self.currentLayer = None
        self.parent = parent

        self.fieldsToPublish = {}
        self.metadata = {}
        execute(self._setupUi)

    def _setupUi(self):
        self.setupUi(self)    
        self.bar = QgsMessageBar()
        self.bar.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.layout().insertWidget(0, self.bar)

        self.txtNoLayers.setVisible(False)

        self.populateComboBoxes()
        self.populateLayers()
        self.listLayers.setContextMenuPolicy(Qt.CustomContextMenu)
        self.listLayers.customContextMenuRequested.connect(self.showContextMenu)
        self.listLayers.currentRowChanged.connect(self.currentRowChanged)
        self.comboGeodataServer.currentIndexChanged.connect(self.geodataServerChanged)
        self.comboMetadataServer.currentIndexChanged.connect(self.metadataServerChanged)
        self.btnPublish.clicked.connect(self.publish)
        self.btnPublishOnBackground.clicked.connect(self.publishOnBackground)
        self.btnOpenQgisMetadataEditor.clicked.connect(self.openMetadataEditor)
        self.labelSelect.linkActivated.connect(self.selectLabelClicked)
        self.btnRemoveAll.clicked.connect(self.unpublishAll)
        self.btnRemoveAll.setIcon(REMOVE_ICON)
        self.btnValidate.setIcon(VALIDATE_ICON)
        self.btnPreview.clicked.connect(self.previewMetadata)
        self.btnPreview.setIcon(PREVIEW_ICON)
        self.btnImport.setIcon(IMPORT_ICON)
        #self.btnImport.setVisible(False)
        self.btnImport.clicked.connect(self.importMetadata)
        self.btnValidate.clicked.connect(self.validateMetadata)
        self.btnUseConstraints.clicked.connect(lambda: self.openMetadataEditor(ACCESS))
        self.btnAccessConstraints.clicked.connect(lambda: self.openMetadataEditor(ACCESS))
        self.btnIsoTopic.clicked.connect(lambda: self.openMetadataEditor(CATEGORIES))
        self.btnKeywords.clicked.connect(lambda: self.openMetadataEditor(KEYWORDS))
        self.btnDataContact.clicked.connect(lambda: self.openMetadataEditor(CONTACT))
        self.btnMetadataContact.clicked.connect(lambda: self.openMetadataEditor(CONTACT))
        self.btnExportFolder.clicked.connect(self.selectExportFolder)    

        if self.listLayers.count():
            item = self.listLayers.item(0)
            self.listLayers.setCurrentItem(item)
            self.currentRowChanged(0)
        else:
            self.txtNoLayers.setVisible(True)
            self.listLayers.setVisible(False)
            self.labelSelect.setVisible(False)
            #self.spacerLayerSelect.setVisible(False)
            self.btnRemoveAll.setVisible(False)

        self.metadataServerChanged()
        self.selectLabelClicked("all")

    def selectExportFolder(self):
        folder = QFileDialog.getExistingDirectory(self, self.tr("Export to folder"))
        if folder:            
            self.txtExportFolder.setText(folder)

    def geodataServerChanged(self):
        self.updateLayersPublicationStatus(True, False)

    def metadataServerChanged(self):
        self.updateLayersPublicationStatus(False, True)
        try:
            profile = metadataServers()[self.comboMetadataServer.currentText()].profile
        except KeyError:
            profile = GeonetworkServer.PROFILE_DEFAULT
        if profile == GeonetworkServer.PROFILE_DEFAULT:
            if self.tabWidgetMetadata.count() == 3:
                self.tabWidgetMetadata.removeTab(1)
                self.tabWidgetMetadata.removeTab(1)
        else:
            if self.tabWidgetMetadata.count() == 1:
                title = "Dutch geography" if profile == GeonetworkServer.PROFILE_DUTCH else "INSPIRE"
                self.tabWidgetMetadata.addTab(self.tabInspire, title)
                self.tabWidgetMetadata.addTab(self.tabTemporal, self.tr("Temporal"))
            self.comboStatus.setVisible(profile == GeonetworkServer.PROFILE_DUTCH)
        
    def selectLabelClicked(self, url):
        state = Qt.Unchecked if url == "none" else Qt.Checked
        for i in range (self.listLayers.count()):
            item = self.listLayers.item(i)
            widget = self.listLayers.itemWidget(item).setCheckState(state)

    def currentRowChanged(self, currentRow):
        if self.currentRow == currentRow:
            return
        self.currentRow = currentRow
        self.storeFieldsToPublish()
        self.storeMetadata()
        layers = self.publishableLayers()
        layer = layers[currentRow]
        self.currentLayer = layer
        self.populateLayerMetadata()
        self.populateLayerFields()
        if layer.type() != layer.VectorLayer:
            self.tabLayerInfo.setCurrentWidget(self.tabMetadata)

    def populateLayerMetadata(self):
        metadata = self.metadata[self.currentLayer]
        self.txtMetadataTitle.setText(metadata.title())
        self.txtAbstract.setPlainText(metadata.abstract())
        isoTopics = ",".join(metadata.keywords().get("gmd:topicCategory", []))
        self.txtIsoTopic.setText(isoTopics)        
        keywords = []
        for group in metadata.keywords().values():
            keywords.extend(group)
        self.txtKeywords.setText(",".join(keywords))
        if metadata.contacts():
            self.txtDataContact.setText(metadata.contacts()[0].name)
            self.txtMetadataContact.setText(metadata.contacts()[0].name)
        self.txtUseConstraints.setText(metadata.fees())
        licenses = metadata.licenses()
        if licenses:
            self.txtAccessConstraints.setText(licenses[0])
        else:
            self.txtAccessConstraints.setText("")
        self.comboLanguage.setCurrentText(metadata.language())
        #TODO: Use default values if no values in QGIS metadata object

    def populateLayerFields(self):
        if self.currentLayer.type() == self.currentLayer.VectorLayer:
            fields = [f.name() for f in self.currentLayer.fields()]
            self.tabLayerInfo.setTabEnabled(1, True)            
            self.tableFields.setRowCount(len(fields))
            for i, field in enumerate(fields):
                item = QTableWidgetItem()
                item.setFlags(item.flags() ^ Qt.ItemIsEditable)
                check = Qt.Checked if self.fieldsToPublish[self.currentLayer][field] else Qt.Unchecked
                item.setCheckState(check)
                self.tableFields.setItem(i, 0, item)
                item = QTableWidgetItem(field)
                item.setFlags(item.flags() ^ Qt.ItemIsEditable)
                self.tableFields.setItem(i, 1, item)
        else:
            self.tabLayerInfo.setTabEnabled(1, False)

    def storeMetadata(self):
        if self.currentLayer is not None:
            metadata = self.metadata[self.currentLayer]
            metadata.setTitle(self.txtMetadataTitle.text())
            metadata.setAbstract(self.txtAbstract.toPlainText())
            metadata.setLanguage(self.comboLanguage.currentText())
            self.currentLayer.setMetadata(metadata)

    def storeFieldsToPublish(self):
        if self.currentLayer is not None:
            if self.currentLayer.type() == self.currentLayer.VectorLayer:
                fieldsToPublish = {}
                fields = self.currentLayer.fields()
                for i in range(fields.count()):
                    chkItem = self.tableFields.item(i, 0)
                    nameItem = self.tableFields.item(i, 1)
                    fieldsToPublish[nameItem.text()] = chkItem.checkState() == Qt.Checked
                self.fieldsToPublish[self.currentLayer] = fieldsToPublish

    def showContextMenu(self, pos):
        item = self.listLayers.itemAt(pos)
        if item is None:
            return
        row = self.listLayers.row(item)
        name = self.listLayers.itemWidget(item).name()
        menu = QMenu()        
        if self.isDataPublished.get(name):
            menu.addAction(self.tr("View WMS layer"), lambda: self.viewWms(name))
            menu.addAction(self.tr("Unpublish data"), lambda: self.unpublishData(name))
        if self.isMetadataPublished.get(name):
            menu.addAction(self.tr("View metadata"), lambda: self.viewMetadata(name))  
            menu.addAction(self.tr("Unpublish metadata"), lambda: self.unpublishMetadata(name))
        if any(self.isDataPublished.values()):
            menu.addAction(self.tr("View all WMS layers"), self.viewAllWms)
        menu.exec_(self.listLayers.mapToGlobal(pos))

    def publishableLayers(self):
        def _layersFromTree(layerTreeGroup):
            _layers = []
            for child in layerTreeGroup.children():                    
                if isinstance(child, QgsLayerTreeLayer):
                    _layers.append(child.layer())            
                elif isinstance(child, QgsLayerTreeGroup):
                    _layers.extend(_layersFromTree(child))

            return _layers
        root = QgsProject.instance().layerTreeRoot()                
        layers = [layer for layer in _layersFromTree(root) 
                if layer.type() in [QgsMapLayer.VectorLayer, QgsMapLayer.RasterLayer]
                and layer.dataProvider().name() != "wms"]
        return layers

    def populateLayers(self):
        layers = self.publishableLayers()
        for i, layer in enumerate(layers):
            fields = [f.name() for f in layer.fields()] if layer.type() == layer.VectorLayer else []
            self.fieldsToPublish[layer] = {f:True for f in fields}
            self.metadata[layer] = layer.metadata().clone()
            self.addLayerListItem(layer)
        self.updateLayersPublicationStatus()

    def addLayerListItem(self, layer):
        widget = LayerItemWidget(layer)
        item = QListWidgetItem(self.listLayers)
        item.setSizeHint(widget.sizeHint())
        self.listLayers.addItem(item)
        self.listLayers.setItemWidget(item, widget)
        return item
        
    def populateComboBoxes(self):
        self.populatecomboMetadataServer()
        self.populatecomboGeodataServer()
        self.comboLanguage.clear()
        for lang in QgsMetadataWidget.parseLanguages():
            self.comboLanguage.addItem(lang)

    def populatecomboGeodataServer(self):
        self.comboGeodataServer.clear()
        self.comboGeodataServer.addItem(self.tr("Do not publish data"))
        self.comboGeodataServer.addItems(geodataServers().keys())

    def populatecomboMetadataServer(self):
        self.comboMetadataServer.clear()
        self.comboMetadataServer.addItem(self.tr("Do not publish metadata"))
        self.comboMetadataServer.addItems(metadataServers().keys())

    def updateServers(self):
        #TODO: do not call updateLayersPublicationStatus if not really needed
        self.comboGeodataServer.currentIndexChanged.disconnect(self.geodataServerChanged)
        self.comboMetadataServer.currentIndexChanged.disconnect(self.metadataServerChanged)
        self.populatecomboMetadataServer()
        current = self.comboGeodataServer.currentText()
        self.populatecomboGeodataServer()
        if current in geodataServers().keys():
            self.comboGeodataServer.setCurrentText(current)
        current = self.comboMetadataServer.currentText()
        self.populatecomboMetadataServer()
        if current in metadataServers().keys():
            self.comboMetadataServer.setCurrentText(current)
        self.updateLayersPublicationStatus()
        self.comboGeodataServer.currentIndexChanged.connect(self.geodataServerChanged)
        self.comboMetadataServer.currentIndexChanged.connect(self.metadataServerChanged)

    def isMetadataOnServer(self, layer):
        try:
            server = metadataServers()[self.comboMetadataServer.currentText()]
            uuid = uuidForLayer(self.layerFromName(layer))
            return server.metadataExists(uuid)
        except KeyError:
            return False
        except:
            return False

    def isDataOnServer(self, layer):
        try:
            server = geodataServers()[self.comboGeodataServer.currentText()] 
            return server.layerExists(layer)            
        except KeyError:            
            return False
        except Exception as e:
            return False

    def importMetadata(self):
        if self.currentLayer is None:
            return
        metadataFile = os.path.splitext(self.currentLayer.source())[0] + ".xml"
        if not os.path.exists(metadataFile):
            metadataFile = self.currentLayer.source() + ".xml"
            if not os.path.exists(metadataFile):
                metadataFile = None
        
        if metadataFile is None:
            ret = QMessageBox.question(self, self.tr("Metadata file"), 
                            self.tr("Could not find a suitable metadata file.\nDo you want to select it manually?"))            
            if ret == QMessageBox.Yes:
                metadataFile, _ = QFileDialog.getOpenFileName(self, self.tr("Metadata file"), 
                                    os.path.dirname(self.currentLayer.source()), '*.xml')
                print(metadataFile)
        
        if metadataFile:
            try:
                loadMetadataFromXml(self.currentLayer, metadataFile)
            except:
                self.bar.pushMessage(self.tr("Error importing metadata"), 
                    self.tr("Cannot convert the metadata file. Maybe not ISO19139 or ESRI-ISO format?"), 
                    level=Qgis.Warning, duration=5)
                return

            self.metadata[self.currentLayer] = self.currentLayer.metadata().clone()
            self.populateLayerMetadata()
            self.bar.pushMessage("", self.tr("Metadata correctly imported"), level=Qgis.Success, duration=5)
        
        
    def validateMetadata(self):
        if self.currentLayer is None:
            return
        self.storeMetadata()
        validator = QgsNativeMetadataValidator()
        result, errors = validator.validate(self.metadata[self.currentLayer])
        if result:
            txt = self.tr("No validation errors")
        else:
            txt = (self.tr("The following issues were found:") + "<br>" + 
                    "<br>".join(["<b>%s</b>:%s" % (err.section, err.note) for err in errors]))
        dlg = QgsMessageOutput.createMessageOutput()
        dlg.setTitle(self.tr("Metadata validation"))
        dlg.setMessage(txt, QgsMessageOutput.MessageHtml)
        dlg.showMessage()

    def openMetadataEditor(self, tab):
        if self.currentLayer is None:
            return        
        self.storeMetadata()
        metadata = self.metadata[self.currentLayer].clone()
        w = MetadataDialog(metadata, tab, self)
        w.exec_()
        if w.metadata is not None:
            self.metadata[self.currentLayer] = w.metadata
            self.populateLayerMetadata()          

    def unpublishData(self, name):
        server = geodataServers()[self.comboGeodataServer.currentText()]
        server.deleteLayer(name)
        server.deleteStyle(name)
        self.updateLayerIsDataPublished(name, False)

    def unpublishMetadata(self, name):
        server = metadataServers()[self.comboMetadataServer.currentText()]
        uuid = uuidForLayer(self.layerFromName(name))
        server.deleteMetadata(uuid)
        self.updateLayerIsMetadataPublished(name, False)

    def updateLayerIsMetadataPublished(self, name, value):
        self.isMetadataPublished[name] = value
        for i in range(self.listLayers.count()):
            item = self.listLayers.item(i)
            widget = self.listLayers.itemWidget(item)
            if widget.name() == name:
                server = metadataServers()[self.comboMetadataServer.currentText()] if value else None
                widget.setMetadataPublished(server)

    def updateLayerIsDataPublished(self, name, value):
        self.isDataPublished[name] = value
        for i in range(self.listLayers.count()):
            item = self.listLayers.item(i)
            widget = self.listLayers.itemWidget(item)
            if widget.name() == name:
                server = geodataServers()[self.comboGeodataServer.currentText()] if value else None
                widget.setDataPublished(server)

    def updateLayersPublicationStatus(self, data=True, metadata=True):
        canPublish = True
        if data:
            try:
                dataServer = geodataServers()[self.comboGeodataServer.currentText()]
                if dataServer.testConnection():
                    self.comboGeodataServer.setStyleSheet("QComboBox { }")
                else:
                    self.comboGeodataServer.setStyleSheet("QComboBox { border: 2px solid red; }")
                    dataServer = None
                    canPublish = False
            except KeyError:
                self.comboGeodataServer.setStyleSheet("QComboBox { }")
                dataServer = None
            
        if metadata:
            try:
                metadataServer = metadataServers()[self.comboMetadataServer.currentText()]
                if metadataServer.testConnection():
                    self.comboMetadataServer.setStyleSheet("QComboBox { }")
                else:
                    self.comboMetadataServer.setStyleSheet("QComboBox { border: 2px solid red; }")
                    metadataServer = None
                    canPublish = False
            except KeyError:
                self.comboMetadataServer.setStyleSheet("QComboBox { }")
                metadataServer = None

        for i in range(self.listLayers.count()):
            item = self.listLayers.item(i)
            widget = self.listLayers.itemWidget(item)
            name = widget.name()
            if data:
                server = None
                if dataServer:
                    self.isDataPublished[name] = self.isDataOnServer(name)
                    server = dataServer if self.isDataPublished[name] else None
                widget.setDataPublished(server)
            if metadata:
                server = None
                if metadataServer:
                    self.isMetadataPublished[name] = self.isMetadataOnServer(name)
                    server = metadataServer if self.isMetadataPublished[name] else None
                widget.setMetadataPublished(server)

        canPublish = canPublish and self.listLayers.count()
        self.btnPublish.setEnabled(canPublish)
        self.btnPublishOnBackground.setEnabled(canPublish)

    def unpublishAll(self):
        for name in self.isDataPublished:
            if self.isDataPublished.get(name, False):
                self.unpublishData(name)
            if self.isMetadataPublished.get(name, False):
                self.unpublishMetadata(name)            

    def viewWms(self, name):        
        layer = self.layerFromName(name)
        names = [layer.name()]        
        bbox = layer.extent()
        if bbox.isEmpty():
            bbox.grow(1)
        sbbox = ",".join([str(v) for v in [bbox.xMinimum(), bbox.yMinimum(), bbox.xMaximum(), bbox.yMaximum()]])        
        server = geodataServers()[self.comboGeodataServer.currentText()]
        server.openPreview(names, sbbox, layer.crs().authid())

    def viewAllWms(self):
        server = geodataServers()[self.comboGeodataServer.currentText()]
        layers = self.publishableLayers()
        bbox = QgsRectangle()
        canvasCrs = iface.mapCanvas().mapSettings().destinationCrs()
        names = []
        for layer in layers:
            if self.isDataPublished[layer.name()]:
                names.append(layer.name())                
                xform = QgsCoordinateTransform(layer.crs(), canvasCrs, QgsProject.instance())
                extent = xform.transform(layer.extent())
                bbox.combineExtentWith(extent)
        sbbox = ",".join([str(v) for v in [bbox.xMinimum(), bbox.yMinimum(), bbox.xMaximum(), bbox.yMaximum()]])
        server.openPreview(names, sbbox, canvasCrs.authid())

    def previewMetadata(self):
        if self.currentLayer is None:
            return        
        html = self.currentLayer.htmlMetadata()
        dlg = QgsMessageOutput.createMessageOutput()
        dlg.setTitle("Layer metadata")
        dlg.setMessage(html, QgsMessageOutput.MessageHtml)
        dlg.showMessage()

    def viewMetadata(self, name):
        server = metadataServers()[self.comboMetadataServer.currentText()]
        layer = self.layerFromName(name)
        uuid = uuidForLayer(layer)
        server.openMetadata(uuid)

    def publish(self):
        toPublish = self._toPublish()
        if self.validateBeforePublication(toPublish):            
            progressDialog = ProgressDialog(toPublish, self.parent)
            task = self.getPublishTask(self.parent)
            task.stepStarted.connect(progressDialog.setInProgress)
            task.stepSkipped.connect(progressDialog.setSkipped)
            task.stepFinished.connect(progressDialog.setFinished)
            progressDialog.show()            
            #task.progressChanged.connect(progress.setValue)
            ret = execute(task.run)     
            progressDialog.close()
            #self.bar.clearWidgets()
            task.finished(ret)
            if task.exception is not None:
                if task.exceptiontype == requests.exceptions.ConnectionError:
                    QMessageBox.warning(self, self.tr("Error while publishing"), 
                            self.tr("Connection error. Server unavailable.\nSee QGIS log for details"))
                else:
                    self.bar.clearWidgets()
                    self.bar.pushMessage(self.tr("Error while publishing"), self.tr("See QGIS log for details"), level=Qgis.Warning, duration=5)
                QgsMessageLog.logMessage(task.exception, 'GeoCat Bridge', level=Qgis.Critical)
            if isinstance(task, PublishTask):
                self.updateLayersPublicationStatus(task.geodataServer is not None, task.metadataServer is not None)

    def publishOnBackground(self):
        if self.validateBeforePublication():
            self.parent.close()
            task = self.getPublishTask(iface.mainWindow())
            def _finished():
                if task.exception is not None:                    
                    iface.messageBar().pushMessage(self.tr("Error while publishing"), self.tr("See QGIS log for details"), level=Qgis.Warning, duration=5)
                    QgsMessageLog.logMessage(task.exception, 'GeoCat Bridge', level=Qgis.Critical)
            task.taskTerminated.connect(_finished)
            QgsApplication.taskManager().addTask(task)
            QCoreApplication.processEvents()

    def validateBeforePublication(self, toPublish):
        names = []
        errors = set()
        for i in range(self.listLayers.count()):            
            item = self.listLayers.item(i)
            widget = self.listLayers.itemWidget(item)
            if widget.checked():
                name = widget.name()
                for c in "?&=#":
                    if c in name:
                        errors.add("Unsupported character in layer name: " + c)
                if name in names:
                    errors.add("Several layers with the same name")
                names.append(name)
        print(0)
        if self.comboGeodataServer.currentIndex() != 0:
            print("a")
            geodataServer = geodataServers()[self.comboGeodataServer.currentText()]
            geodataServer.validateGeodataBeforePublication(errors, toPublish)


        if self.comboMetadataServer.currentIndex() != 0:
            metadataServer = metadataServers()[self.comboMetadataServer.currentText()]
            metadataServer.validateMetadataBeforePublication(errors)
        
        if errors:
            txt = '''<p><b>Cannot publish data.</b></p>
                    <p>The following issues were found:<p><ul><li>%s</li></ul>
                    ''' % "</li><li>".join(errors)
            dlg = QgsMessageOutput.createMessageOutput()
            dlg.setTitle("Publish")
            dlg.setMessage(txt, QgsMessageOutput.MessageHtml)
            dlg.showMessage()
            return False
        else:
            return True

    def _toPublish(self):
        toPublish = []
        for i in range(self.listLayers.count()):            
            item = self.listLayers.item(i)
            widget = self.listLayers.itemWidget(item)
            if widget.checked():
                name = widget.name()   
                toPublish.append(name)
        return toPublish

    def getPublishTask(self, parent):
        self.storeMetadata()
        self.storeFieldsToPublish()

        toPublish = self._toPublish()

        if self.tabOnOffline.currentIndex() == 0:
            if self.comboGeodataServer.currentIndex() != 0:
                geodataServer = geodataServers()[self.comboGeodataServer.currentText()]
            else:
                geodataServer = None

            if self.comboMetadataServer.currentIndex() != 0:
                metadataServer = metadataServers()[self.comboMetadataServer.currentText()]
            else:
                metadataServer = None 

            onlySymbology = self.chkOnlySymbology.checkState() == Qt.Checked

            return PublishTask(toPublish, self.fieldsToPublish, onlySymbology, geodataServer, metadataServer, parent)
        else:
            return ExportTask(self.txtExportFolder.text(), toPublish, self.fieldsToPublish, self.chkExportData.isChecked(),
                                self.chkExportMetadata.isChecked(), self.chkExportSymbology.isChecked())


    def layerFromName(self, name):
        layers = self.publishableLayers()
        for layer in layers:
            if layer.name() == name:
                return layer


class LayerItemWidget(QWidget):
    def __init__ (self, layer, parent = None):
        super(LayerItemWidget, self).__init__(parent)
        self.layer = layer
        self.layout = QHBoxLayout()
        self.check = QCheckBox()
        self.check.setText(layer.name())
        if layer.type() == layer.VectorLayer:
            self.check.setIcon(QgsApplication.getThemeIcon('/mIconLineLayer.svg'))
        else:
            self.check.setIcon(QgsApplication.getThemeIcon('/mIconRaster.svg'))
        self.labelMetadata = QLabel()
        self.labelMetadata.setFixedWidth(50)
        self.labelData = QLabel()
        self.labelData.setFixedWidth(50)
        self.layout.addWidget(self.check)
        self.layout.addWidget(self.labelData)
        self.layout.addWidget(self.labelMetadata)
        self.setLayout(self.layout)
        
    def name(self):
        return self.layer.name()

    def iconPath(self, server):
        return os.path.join(os.path.dirname(os.path.dirname(__file__)), "icons", 
                        "%s.png" % server.__class__.__name__.lower()[:-6])

    def setMetadataPublished(self, server):
        self.labelMetadata.setPixmap(QPixmap(self.iconPath(server)))

    def setDataPublished(self, server):
        self.labelData.setPixmap(QPixmap(self.iconPath(server)))

    def checked(self):
        return self.check.isChecked()

    def setCheckState(self, state):
        self.check.setCheckState(state)
