import datetime
import json
import os

import numpy as np
import pandas as pd
from PyQt5.QtCore import QObject, QDir, pyqtSignal
from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import QFileDialog, QMessageBox

from source import genutils
from source.Annotation import Annotation
from source.Blob import Blob
from source.Channel import Channel
from source.Label import Label
from source.Point import Point
from source.Correspondences import Correspondences
from source.Genet import Genet
from source.Grid import Grid
from source.Image import Image
from source.Label import Label
from source.RegionAttributes import RegionAttributes
from source.Shape import Layer, Shape
from source.tools import Cut

def loadProject(taglab_working_dir, filename, default_dict):
    dir = QDir(taglab_working_dir)
    abspath = os.path.join(taglab_working_dir, dir.relativeFilePath(filename))
    f = open(abspath, "r")
    try:
        data = json.load(f)
    except json.JSONDecodeError as e:
        raise Exception(str(e))

    if "Map File" in data:
        project = loadOldProject(taglab_working_dir, data)
        project.loadDictionary(os.path.join(taglab_working_dir, default_dict))
        project.region_attributes = RegionAttributes()
    else:
        project = Project(**data)

    f.close()

    if project.dictionary_name == "":
        project.dictionary_name = "My dictionary"

    project.filename = filename

    # check if a file exist for each image and each channel
    new_dir = None
    dir = QDir(taglab_working_dir)
    for image in project.images:
        for channel in image.channels:
            if not os.path.exists(channel.filename) and len(channel.filename) > 4:

                if new_dir is None:

                    (filename, filter) = QFileDialog.getOpenFileName(None,
                                                                     "Couldn't find " + channel.filename + " please select it:",
                                                                     taglab_working_dir,
                                                                     "Image Files (*.png *.jpg *.jpeg *.tif *.tiff)")

                    new_dir = os.path.dirname(filename)

                else:

                    filename = channel.filename
                    filename = os.path.basename(filename)
                    filename = os.path.join(new_dir, filename)

                channel.filename = dir.relativeFilePath(filename)

                if image.georef_filename != "":
                    basename = os.path.basename(image.georef_filename)
                    image.georef_filename = dir.relativeFilePath(os.path.join(new_dir, basename))

                image.georef_filename = ""

    # load geo-reference information
    for im in project.images:
        if im.georef_filename != "":
            im.loadGeoInfo(im.georef_filename)

    # ensure all maps have an ID:
    count = 1
    for im in project.images:
        if im.id is None:
            im.id = "Map " + str(count)
        count += 1

    # ensure all maps have a name
    count = 1
    for im in project.images:
        if im.name is None or im.name == "":
            im.name = "noname{:02d}".format(count)
        count += 1

    # pixel size MUST BE a string
    im.map_px_to_mm_factor = str(im.map_px_to_mm_factor)

    # ensure all maps have an acquisition date
    for im in project.images:
        if not genutils.isValidDate(im.acquisition_date):
            im.acquisition_date = "1955-11-05"

    # ensure the maps are ordered by the acquisition date
    project.orderImagesByAcquisitionDate()

    return project


# WARNING!! The old-style projects do not include a labels dictionary
def loadOldProject(taglab_working_dir, data):
    project = Project()
    map_filename = data["Map File"]

    # convert to relative paths in case:
    dir = QDir(taglab_working_dir)
    map_filename = dir.relativeFilePath(map_filename)
    image_name = os.path.basename(map_filename)
    image_name = image_name[:-4]
    image = Image(id=image_name)
    image.map_px_to_mm_factor = data["Map Scale"]
    image.acquisition_date = "1955-11-05"
    channel = Channel(filename=map_filename, type="RGB")
    image.channels.append(channel)

    for blob_data in data["Segmentation Data"]:
        blob = Blob(None, 0, 0, 0)
        blob.fromDict(blob_data)
        blob.setId(int(blob.id))  # id should be set again to update related info
        image.annotations.addBlob(blob)

    project.images.append(image)
    return project


class ProjectEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Image):
            return obj.save()
        elif isinstance(obj, Channel):
            return obj.save()
        elif isinstance(obj, Label):
            return obj.save()
        elif isinstance(obj, Annotation):
            return obj.save()
        elif isinstance(obj, Point):
            return obj.save()
        elif isinstance(obj, Blob):
            return obj.save()
        elif isinstance(obj, Layer):
            return obj.save()
        elif isinstance(obj, Shape):
            return obj.save()
        elif isinstance(obj, Correspondences):
            return obj.save()
        elif isinstance(obj, Grid):
            return obj.save()
        elif isinstance(obj, Genet):
            return {}
        elif isinstance(obj, RegionAttributes):
            return obj.save()
        return json.JSONEncoder.default(self, obj)


class Project(QObject):

    # custom signals
    blobAdded = pyqtSignal(Image, Blob)
    pointAdded = pyqtSignal(Image, Point)
    blobRemoved = pyqtSignal(Image, Blob)
    pointRemoved = pyqtSignal(Image, Point)
    blobUpdated = pyqtSignal(Image, Blob, Blob)
    blobClassChanged = pyqtSignal(Image, str, Blob)
    pointClassChanged = pyqtSignal(Image, str, Point)
    blobClassChangedByGenet = pyqtSignal(Image, str, Blob)
    correspTableChanged = pyqtSignal()

    def __init__(self, filename=None, labels={}, images=[], correspondences=None,
                 spatial_reference_system=None, metadata={}, image_metadata_template={}, genet={},
                 dictionary_name="", dictionary_description="", working_area=None, region_attributes={},
                 markers={}, parent=None):
        super(Project, self).__init__(parent)

        self.filename = None  # filename with path of the project json

        # area of the images where the user annotate the data
        # NOTE 1: since the images are co-registered the working area is the same for all the images
        # NOTE 2: the working area is a RECTANGULAR region stored as [top, left, width, height]
        self.working_area = working_area

        self.dictionary_name = dictionary_name
        self.dictionary_description = dictionary_description

        self.labels = {key: Label(**value) for key, value in labels.items()}
        if not 'Empty' in self.labels:
            self.labels['Empty'] = Label(id='Empty', name='Empty', description=None, fill=[127, 127, 127],
                                         border=[200, 200, 200], visible=True)

        # compatibility with previous TagLab versions (working_area does not exist anymore)

        self.images = list()

        for img in images:
            if img.get("working_area") is not None:
                img.__delitem__("working_area")

            self.images.append(Image(**img))

        # dict of tables (DataFrame) of correspondences between a source and a target image

        self.correspondences = {}
        if correspondences is not None:
            for key in correspondences.keys():
                source = correspondences[key]['source']
                target = correspondences[key]['target']
                self.correspondences[key] = Correspondences(self.getImageFromId(source), self.getImageFromId(target))
                self.correspondences[key].fillTable(correspondences[key]['correspondences'])

        self.genet = Genet(self)
        self.region_attributes = RegionAttributes(**region_attributes)

        self.spatial_reference_system = spatial_reference_system  # if None we assume coordinates in pixels (but Y is up or down?!)
        self.metadata = metadata  # project metadata => keyword -> value
        self.image_metadata_template = image_metadata_template  # description of metadata keywords expected in images
        # name: { type: (integer, date, string), mandatory: (true|false), default: ... }

        self.markers = markers  # Store alignment markers with 'ref' & 'coreg' images

    def importLabelsFromConfiguration(self, dictionary):
        """
        This function should be removed when the Labels Panel will be finished.
        """
        self.labels = {}
        if not 'Empty' in dictionary:
            self.labels['Empty'] = Label(id='Empty', name='Empty', description=None, fill=[127, 127, 127],
                                         border=[200, 200, 200], visible=True)
        for key in dictionary.keys():
            color = dictionary[key]
            self.labels[key] = Label(id=key, name=key, description=None, fill=color, border=[200, 200, 200],
                                     visible=True)

    # def checkDictionaryConsistency(self, labels):
    #     """
    #     Check the consistency between a list of labels and the current annotations.
    #     """
    #
    #     messages = ""
    #     inconsistencies = 0
    #
    #     # check for existing labels present in the annotations but not present in list of labels
    #
    #     for class_name in class_names:
    #         if not class_name in labels:
    #             msg = "\n" + str(inconsistencies) + ") Label '" + key + "' is missing. We automatically add it."
    #             messages += msg
    #             inconsistencies += 1
    #
    #             label = self.labels[class_name]
    #             labels.append(label.copy())
    #
    #     if inconsistencies > 0:
    #         box = QMessageBox()
    #         box.setWindowTitle("There are dictionary inconsistencies. See below:\n")
    #         box.setText(messages)
    #         box.exec()
    #
    #     return True

    def labelsInUse(self):
        """
        It returns the labels currently assigned to the annotations.
        """

        class_names = set()  # class names effectively used
        for image in self.images:
            for blob in image.annotations.seg_blobs:
                class_names.add(blob.class_name)

        if len(class_names) == 0:
            return []
        else:
            return list(class_names)

    def save(self, filename=None):

        # check inconsistencies. They can be caused by bugs during the regions update/editing
        if self.correspondences is not None:
            for key in self.correspondences.keys():
                if self.correspondences[key].checkTable() is True:
                    # there are inconsistencies, THIS MUST BE NOTIFIED
                    msgBox = QMessageBox()
                    msgBox.setWindowTitle("INCONSISTENT CORRESPONDENCES")
                    msgBox.setText(
                        "Inconsistent correspondences has been found !!\nPlease, Notify this problem to the TagLab developers.")
                    msgBox.exec()

        data = self.__dict__
        str = json.dumps(data, cls=ProjectEncoder, indent=1)

        if filename is None:
            filename = self.filename
        f = open(filename, "w")
        f.write(str)
        f.close()

    def loadDictionary(self, filename):
        """
        It returns True if the dictionary is opened correctly, otherwise it returns False.
        """

        try:
            f = open(filename)
            dictionary = json.load(f)
            f.close()

            self.dictionary_name = dictionary['Name']
            self.dictionary_description = dictionary['Description']
            labels = dictionary['Labels']

            self.labels = {}
            for label in labels:
                id = label['id']
                name = label['name']
                fill = label['fill']
                border = label['border']
                description = label['description']
                self.labels[name] = Label(id=id, name=name, fill=fill, border=border)

        except Exception as e:
            QMessageBox.critical(None, "Error", f"Error loading dictionary: {filename}\n{e}")
            return False

        return True

    def setDictionaryFromListOfLabels(self, labels):
        """
        Convert the list of labels into a labels dictionary.
        """

        self.labels = {}

        label_names = []
        for label in labels:
            label_names.append(label.name)

        # 'Empty' key must be be always present
        if not 'Empty' in label_names:
            self.labels['Empty'] = Label(id='Empty', name='Empty', description=None, fill=[127, 127, 127],
                                         border=[200, 200, 200], visible=True)

        for label in labels:
            self.labels[label.name] = label

    def classColor(self, class_name):
        if class_name == "Empty":
            return [127, 127, 127]
        if not class_name in self.labels:
            raise ("Missing label for " + class_name)
        return self.labels[class_name].fill

    def classBrushFromName(self, blob):
        brush = QBrush()
        if blob.class_name == "Empty":
            return brush

        if not blob.class_name in self.labels:
            print("Missing label for " + blob.class_name + ". Creating one.")
            self.labels[blob.class_name] = Label(blob.class_name, blob.class_name, fill=[255, 0, 0])

        color = self.labels[blob.class_name].fill
        brush = QBrush(QColor(color[0], color[1], color[2], 200))
        return brush


    def isLabelVisible(self, id):
        if not id in self.labels:
            print("WARNING! Unknown label: " + id)

        lbl = self.labels.get(id)
        return self.labels[id].visible

    def orderImagesByAcquisitionDate(self):
        """
        Order the image list by the acquisition date, from the oldest to the newest.
        """
        if self.images is not None:
            if len(self.images) > 1:
                image_list = self.images
                #                image_list.sort(key=lambda x: datetime.date.fromisoformat(x.acquisition_date))
                image_list.sort(key=lambda x: datetime.datetime.strptime(x.acquisition_date, '%Y-%m-%d'))

                self.images = image_list

    def indexByImageName(self, image_name):
        """
        Returns the position in the list given the image name.
        """

        for index, image in self.images:
            if image.name == image_name:
                return index

        return -1  # it should never happen (!)

    def addNewImage(self, image, sort=True):
        """
        Annotated images in the image list are sorted by date.
        """
        self.images.append(image)
        if sort:
            self.orderImagesByAcquisitionDate()

    def deleteImage(self, image):
        self.images = [i for i in self.images if i != image]
        self.correspondences = {key: corr for key, corr in self.correspondences.items() if
                                corr.source != image and corr.target != image}
        if image.id in self.markers:
            del self.markers[image.id]

    def findCorrespondences(self, image):

        corresps = list(filter(lambda i, image=image: i.source == image or i.target == image,
                               self.correspondences.values()))
        return corresps

    def findCorrespTables(self, img):
        """
        Given an image it can return zero, one, or two tables of correspondences.
        """

        corresp_tables = []
        for key, corresp in self.correspondences.items():
            if corresp.target == img or corresp.source == img:
                corresp_tables.append(corresp)

        return corresp_tables

    def updateGenets(self):
        """
        Update the genets information in the regions and in the correspondences' tables.
        """

        self.genet.updateGenets()

    def assignClassByGenet(self, class_name, genet_id):
        """
        The given class is assigned to all the regions with the same genet id.
        """

        for key, table in self.correspondences.items():
            rows = table.data[table.data['Genet'] == genet_id]
            for index, row in rows.iterrows():
                blob1_id = row['Blob1']
                blob2_id = row['Blob2']
                blob1 = table.sourceBlobsById([blob1_id])
                if len(blob1) > 0:
                    old_class_name = blob1[0].class_name
                    blob1[0].class_name = class_name
                    self.blobClassChanged.emit(table.source, old_class_name, blob1[0])
                    self.blobClassChangedByGenet.emit(table.source, old_class_name, blob1[0])
                    table.source.annotations.table_needs_update = True

                blob2 = table.targetBlobsById([blob2_id])
                if len(blob2) > 0:
                    old_class_name = blob2[0].class_name
                    blob2[0].class_name = class_name
                    self.blobClassChanged.emit(table.target, old_class_name, blob2[0])
                    self.blobClassChangedByGenet.emit(table.target, old_class_name, blob2[0])
                    table.target.annotations.table_needs_update = True

            rows_index = rows.index
            table.data.loc[rows_index, 'Class'] = class_name


    def addBlob(self, img, blob, notify=True):

        # update image annotations
        img.annotations.addBlob(blob)

        if notify:
            self.blobAdded.emit(img, blob)

    def removeBlob(self, img, blob, notify=True):

        img.annotations.removeAnn(blob)

        if notify:
            self.blobRemoved.emit(img, blob)

    def updateBlob(self, img, old_blob, new_blob, notify=True):

        # update image annotations
        img.annotations.updateBlob(old_blob, new_blob)

        self.updateCorrespondences("UPDATE", img, [new_blob], old_blob)

        if notify:
            self.blobUpdated.emit(img, old_blob, new_blob)

    def setBlobClass(self, img, blob, class_name, notify=True):

        if blob.class_name == class_name:
            return
        else:
            old_blob = blob.copy()

            old_class_name = blob.class_name
            img.annotations.setBlobClass(blob, class_name)

            self.updateCorrespondences("CLASS_CHANGED", img, [blob], None, class_name)

            if notify:
                self.blobClassChanged.emit(img, old_class_name, blob)

    def updateCorrespondences(self, operation, img, blobs_added, blob_removed, class_name=""):

        flag_update_table = False

        if operation == "ADD":
            # WARNING: correspondences are not updated automatically!
            # The involved tables are updated considering the blobs added a 'born' or 'dead'.
            # Eventual correspondence must be added manually by the user!
            # TODO: ADD MESSAGE FOR THE USER
            for blob in blobs_added:
                blob.correspondence_to_check = True

            corresp_tables = self.findCorrespTables(img)
            if len(corresp_tables) > 0:
                for table in corresp_tables:
                    is_source = table.isSource(img)
                    if is_source is True:
                        table.set(blobs_added, [])
                    else:
                        table.set([], blobs_added)

                self.updateGenets()

                flag_update_table = True

        elif operation == "REMOVE":
            # All the involved rows are deleted. Target and source blobs are connected together if any.
            # The genet is recomputed for consistency.

            corresp_tables = self.findCorrespTables(img)

            if len(corresp_tables) > 0:
                for table in corresp_tables:
                    is_source = table.isSource(img)
                    source_blobs_ids, target_blobs_ids, rows_indexes = table.findCluster(blob_removed.id, is_source)
                    if is_source:
                        source_blobs_ids.remove(blob_removed.id)
                    else:
                        target_blobs_ids.remove(blob_removed.id)

                    table.deleteRows(rows_indexes)
                    source_blobs = table.sourceBlobsById(source_blobs_ids)
                    target_blobs = table.targetBlobsById(target_blobs_ids)
                    table.set(source_blobs, target_blobs)

                    blobs = source_blobs + target_blobs
                    for blob in blobs:
                        blob.correspondence_to_check = True

                self.updateGenets()

                flag_update_table = True

        elif operation == "UPDATE":
            # The content of the correspondences tables involved is updated, blobs_added contains the new information,
            # blob_removed the old information.
            corresp_tables = self.findCorrespTables(img)
            if len(corresp_tables) > 0:
                for table in corresp_tables:
                    table.updateBlobId(img, blob_removed.id, blobs_added[0].id)
                    table.updateBlobArea(img, blob_removed.id, blobs_added[0].area, blobs_added[0].surface_area)

                flag_update_table = True

        elif operation == "REPLACE":
            # The blobs added are connected automatically with the ones of the blob removed (if more than one blob is
            # removed, the removed_blob is a list of blobs)
            # The genet is recomputed for consistency.

            corresp_tables = self.findCorrespTables(img)

            if len(corresp_tables) > 0:

                if len(blob_removed) == 1:
                    # one blob is replaced by N blobs, for example this is the case of a CUT operation

                    for table in corresp_tables:

                        is_source = table.isSource(img)

                        source_blobs_ids, target_blobs_ids, rows_indexes = table.findCluster(blob_removed[0].id, is_source)

                        # remove the current connections from the correspondences' table
                        table.deleteRows(rows_indexes)

                        # create the new correspondences
                        if is_source:
                            source_blobs_ids.remove(blob_removed[0].id)
                        else:
                            target_blobs_ids.remove(blob_removed[0].id)

                        source_blobs = table.sourceBlobsById(source_blobs_ids)
                        target_blobs = table.targetBlobsById(target_blobs_ids)

                        if is_source:
                            table.set(blobs_added, target_blobs)
                        else:
                            table.set(source_blobs, blobs_added)

                        blobs = blobs_added + source_blobs + target_blobs
                        for blob in blobs:
                            blob.correspondence_to_check = True
                else:
                    # some blobs are replaced by one blob, for example this is the case of a MERGE operation
                    for table in corresp_tables:

                        is_source = table.isSource(img)

                        # find all the connections
                        all_source_blobs_ids = []
                        all_target_blobs_ids = []
                        all_rows = []
                        for blob in blob_removed:
                            source_blobs_ids, target_blobs_ids, rows_indexes = table.findCluster(blob.id, is_source)

                            # new correspondences to crea
                            if is_source:
                                source_blobs_ids.remove(blob.id)
                            else:
                                target_blobs_ids.remove(blob.id)

                            all_source_blobs_ids += source_blobs_ids
                            all_target_blobs_ids += target_blobs_ids
                            all_rows += rows_indexes

                        # remove such rows from the correspondences' table
                        table.deleteRows(all_rows)

                        # remove duplicated ids
                        all_source_blobs_ids = list(set(all_source_blobs_ids))
                        all_target_blobs_ids = list(set(all_target_blobs_ids))

                        # re-create the connections
                        source_blobs = table.sourceBlobsById(all_source_blobs_ids)
                        target_blobs = table.targetBlobsById(all_target_blobs_ids)

                        if is_source:
                            table.set(blobs_added, target_blobs)
                        else:
                            table.set(source_blobs, blobs_added)

                        blobs = blobs_added + source_blobs + target_blobs
                        for blob in blobs:
                            blob.correspondence_to_check = True

                self.updateGenets()

                flag_update_table = True

        elif operation == "CLASS_CHANGED":
            # The class is updated along the entire temporal sequence.
            blob = blobs_added[0]
            self.assignClassByGenet(class_name, blob.genet)
            flag_update_table = True

        else:
            print("Update correspondences WARNING! -> unknown operation")

        # update the table in the comparison panel
        if flag_update_table:
            self.correspTableChanged.emit()

    def addPoint(self, img, point, notify=True):

        # update image annotations
        img.annotations.addPoint(point)

        if notify:
            self.pointAdded.emit(img, point)

    def removePoint(self, img, point, notify=True):

        # update image annotations
        img.annotations.removeAnn(point)

        if notify:
            self.pointRemoved.emit(img, point)

    def setPointClass(self, img, point, class_name, notify=True):

        if point.class_name == class_name:
            return
        else:
            old_class_name = point.class_name
            img.annotations.setPointClass(point, class_name)

            if notify:
                self.pointClassChanged.emit(img, old_class_name, point)

    def addSamplingAreas(self, img, sampling_areas):
        """
        Add the sampling area to the given image.
        """

        for sampling_area in sampling_areas:
            img.sampling_areas.append(sampling_area)

    def removeSamplingArea(self, img):
        """
        Remove all the sampling areas from the given image.
        """
        img.sampling_area = []

    def getImageFromId(self, id):
        for img in self.images:
            if img.id == id:
                return img
        return None

    def createCorrespondencesTable(self, img_source_idx, img_target_idx):

        key = self.images[img_source_idx].id + "-" + self.images[img_target_idx].id
        corr = Correspondences(self.images[img_source_idx], self.images[img_target_idx])

        if self.correspondences is None:
            self.correspondences = {}

        self.correspondences[key] = corr

        return corr

    def clearComparisonTable(self,key):

        if self.correspondences.get(key):
          del self.correspondences[key]

        else:
            return


    def updateTableKey(self, oldname, newname):

        keys = self.correspondences.copy()
        if self.correspondences:
            for key in keys:
                if key.find(oldname)>=0:
                    table = self.correspondences[key]
                    newkey = key.replace(oldname, newname)
                    self.correspondences[newkey] = table
                    del self.correspondences[key]
        else:
            return


    def getImagePairCorrespondences(self, img_source_idx, img_target_idx):
        """
        Given two image indices returns the current correspondences table is returned.
        Note that the correspondences between the image A and the image B are not the same of the image B and A.
        """
        key = self.images[img_source_idx].id + "-" + self.images[img_target_idx].id

        corresp_table = None
        if self.correspondences :
            corresp_table = self.correspondences.get(key)

        return corresp_table

    def addCorrespondences(self, corresp_table, blobs1, blobs2):
        """
        Add a correspondences to the current ones.
        """

        blobs = blobs1 + blobs2
        for blob in blobs:
            blob.correspondence_to_check = False

        corresp_table.set(blobs1, blobs2)
        self.genet.updateGenets()

    def updatePixelSizeInCorrespondences(self, image, flag_surface_area):

        corresp_tables = self.findCorrespTables(image)
        for corr in corresp_tables:
            corr.updateAreas(use_surface_area=flag_surface_area)

    def computeCorrespondences(self, img_source_idx, img_target_idx):
        """
        Compute the correspondences between an image pair.
        """

        conversion1 = self.images[img_source_idx].pixelSize()
        conversion2 = self.images[img_target_idx].pixelSize()

        # switch form px to mm just for calculation (except areas that are in cm)

        blobs1 = []
        for blob in self.images[img_source_idx].annotations.seg_blobs:
            blob_c = blob.copy()
            blob_c.bbox = (blob_c.bbox * conversion1).round().astype(int)
            blob_c.contour = blob_c.contour * conversion1
            blob_c.area = blob_c.area * conversion1 * conversion1 / 100
            blobs1.append(blob_c)

        blobs2 = []
        for blob in self.images[img_target_idx].annotations.seg_blobs:
            blob_c = blob.copy()
            blob_c.bbox = (blob_c.bbox * conversion2).round().astype(int)
            blob_c.contour = blob_c.contour * conversion2
            blob_c.area = blob_c.area * conversion2 * conversion2 / 100
            blobs2.append(blob_c)

        # create correspondences table
        if self.correspondences is None:
            self.correspondences = {}

        corr = self.createCorrespondencesTable(img_source_idx, img_target_idx)

        corr.autoMatch(blobs1, blobs2)
        #corr.autoMatchM(blobs1, blobs2)   # autoMatchM resolves matching taking into account live/dead specimens (class name constraint is removed)

        lines = corr.correspondences + corr.dead + corr.born

        if len(lines) > 0:
            corr.data = pd.DataFrame(lines, columns=corr.data.columns)
            corr.sort_data()
            corr.correspondence = []
            corr.dead = []
            corr.born = []

        self.genet.updateGenets()

    def create_labels_table(self, image):

        '''
        It creates a data table for the label panel.
        If an active image is given, some statistics are added.
        '''

        dict = {
            'Visibility': np.zeros(len(self.labels), dtype=int),
            'Color': [],
            'Class': [],
            '#R': np.zeros(len(self.labels), dtype=int),
            '#P': np.zeros(len(self.labels), dtype=int),
            'Coverage': np.zeros(len(self.labels),dtype=float)
        }

        for i, key in enumerate(list(self.labels.keys())):
            label = self.labels[key]
            dict['Visibility'][i] = int(label.visible)
            dict['Color'].append(str(label.fill))
            dict['Class'].append(label.name)

            if image is None:
                count = 0
                countP= 0
                new_area = 0.0
            else:
                count, new_area = image.annotations.calculate_perclass_blobs_value(label, image.pixelSize())
                countP = image.annotations.countPoints(label)

            dict['#R'][i] = count
            dict['#P'][i] = countP
            dict['Coverage'][i] = new_area



        # create dataframe
        df = pd.DataFrame(dict, columns=['Visibility', 'Color', 'Class', '#R', '#P', 'Coverage'])
        return df

    def addOrUpdateMarkers(self, refImgId, refMarkers, coregImgId, coregMarkers, markersTypes):
        """
        Insert or Update markers ([x,y] positions) for registration purposes.
        - Markers must have the following format [[x1,y1,w1], [x2,y2,w2], ...];
        - The two lists must be parallel;
        - Positions must be in pixels (relative to the image they are referring to);
        - Weights must be the 'typ' identifier of the MarkerObjData class inside
          the QtAlignmentWidget (MarkerObjData.SOFT_MARKER, MarkerObjData.HARD_MARKER);
        :param refImgId: Id of the 'reference' image
        :param refMarkers: List of markers on the 'reference' image
        :param coregImgId: Id of the 'registered' image
        :param coregMarkers: List of markers on the 'registered' image
        :param markersTypes: List of markers types
        :return: None
        """
        ref = self.images[refImgId]
        coreg = self.images[coregImgId]
        if ref.id not in self.markers:
            self.markers[ref.id] = {}
        self.markers[ref.id][coreg.id] = {
            "markers": {
                "ref": [{
                    "x": x,
                    "y": y,
                } for [x, y] in refMarkers],
                "coreg": [{
                    "x": x,
                    "y": y,
                } for [x, y] in coregMarkers],
                "type": [t for t in markersTypes],
            }
        }

    def retrieveMarkersOrEmpty(self, refImgId, coregImgId):
        """
        Retrieves the markers list (if any) identifying the registration with:
        - 'refImgId' as reference image;
        - 'coregImgId' as registered image;
        The markers list has the following format (for each marker):
        - 'refPos' the [x,y] position (in pixel) on the 'ref' image;
        - 'coregPos' the [x,y] position (in pixel) on the 'coreg' image;
        - 'type' as the marker's type (ex. QtAlignmentWidget.MarkerObjData.SOFT_MARKER);
        :param refImgId: Id of the 'reference' image
        :param coregImgId: Id of the 'registered' image
        :return: markers list or empty list
        """
        if refImgId not in self.markers:
            return []

        if coregImgId not in self.markers[refImgId]:
            return []

        markers = self.markers[refImgId][coregImgId]['markers']
        return [{
            'refPos': [r['x'], r['y']],
            'coregPos': [c['x'], c['y']],
            'type': t,
        } for (r, c, t) in zip(markers['ref'], markers['coreg'], markers['type'])]