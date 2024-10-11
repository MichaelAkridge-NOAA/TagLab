import numpy as np
import torch
import matplotlib.pyplot as plt
import cv2
import sys
import os
from skimage import measure
from PyQt5.QtCore import Qt, pyqtSignal
from source.Mask import paintMask, jointBox, jointMask, replaceMask, checkIntersection, intersectMask
from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtGui import QPen, QBrush
from source import genutils
from source.tools.Tool import Tool
from source.Blob import Blob

from PyQt5.QtCore import Qt, QObject, QPointF, QRectF, QFileInfo, QDir, pyqtSlot, pyqtSignal, QT_VERSION_STR
from PyQt5.QtGui import QImage, QPixmap, QPainter, QPainterPath, QPen, QBrush, QCursor, QColor
from PyQt5.QtWidgets import QApplication, QGraphicsView, QGraphicsItem, QGraphicsScene, QFileDialog, QGraphicsPixmapItem


sys.path.append("..")
from segment_anything import sam_model_registry, SamAutomaticMaskGenerator, SamPredictor
import time

class Sam(Tool):

    samEnded = pyqtSignal()
    

    def __init__(self, viewerplus):
        super(Sam, self).__init__(viewerplus)

        self.viewerplus.mouseMoved.connect(self.handlemouseMove)

        #1024x1024 rect_item size
        self.width = 1024
        self.height = 1024

        self.offset = [0, 0]

        self.rect_item = None
        # self.rect_item = viewerplus.scene.addRect(0, 0, 2048, 2048, QPen(Qt.black, 5, Qt.DotLine)) 
        # self.center_item = viewerplus.scene.addEllipse(0, 0, 10,10, QPen(Qt.black), QBrush(Qt.red))
        
        self.seed_nr = 0
        self.not_overlapping = []
        
        message = "To resize windows cursor: Hold Shift and Mouse Wheel<br><br>\
            To apply SAM segmentation on the area inside the window cursor:<br>\
            mouse left button + Spacebar"
        self.tool_message = f'<div style="text-align: left;">{message}</div>'
       

        """
         Sam parameters: 
            pred_iou_thresh (float): A filtering threshold in [0,1], using the model's predicted mask quality.
            stability_score_thresh (float): A filtering threshold in [0,1], using the stability of the mask under changes to the cutoff used to binarize  the model's mask predictions.
            stability_score_offset (float): The amount to shift the cutoff when calculated the stability score.
            box_nms_thresh (float): The box IoU cutoff used by non-maximal suppression to filter duplicate masks.
            
        IDEA: SE ZOOM LEVEL 0 ALLORA FA TUTTA IMMAGINE 
              SE ZOOM LEVEL E' X >>  1024 AVVISA CHE è GROSSA
              SE ZOMM LEVEL +- 1024 allora prende 1024
              se zoom level << 1024 sovracampiona a 1024 (lo fa lui già mi sA) E CONTA 
            
        
        """

        #add working area
        self.sam_net = None
        self.device = None
        self.created_blobs = []

    def loadNetwork(self):

        if self.sam_net is None:

            self.infoMessage.emit("Loading SAM network..")
            # add choices related to GPU MEMORY

            # sam_checkpoint = "sam_vit_b_01ec64.pth"
            # model_type = "vit_b"

            # sam_checkpoint = "sam_vit_l_0b3195.pth"
            # model_type = "vit_l"

            sam_checkpoint = "sam_vit_h_4b8939.pth"
            model_type = "vit_h"

            models_dir = "models/"
            network_name = os.path.join(models_dir, sam_checkpoint)

            #
            # if not torch.cuda.is_available():
            #     print("CUDA NOT AVAILABLE!")
            #     device = torch.device("cpu")
            # else:
            #     device = torch.device("cuda:0")

            self.device = "cuda"
            self.sam_net = sam_model_registry[model_type](checkpoint=network_name)
            self.sam_net.to(device=self.device)

            # CAPIRE DIFFERENZE DA DEMO   !!!!!!!!!!!

            # # try:
            # #     self.sam_net = genutils.load_is_model(model_path, device, cpu_dist_maps=False)
            #     self.sam_net = sam_model_registry[model_type](checkpoint=model_name)
            #     self.sam_net.to(device=self.device)


            # except Exception as e:
            #     box = QMessageBox()
            #     box.setText("Could not load Sam network. You might need to run update.py.")
            #     box.exec()
            #     return False

        return True

    # def reset(self):
    #     """
    #     Reset net, tools and wa
    #     """
    #
    #     self.resetNetwork()
    #     #self.viewerplus.resetTools()
    #     ##self.resetWorkArea()

    #remove blobs on the edge of the rectangle cursor
    def removeEdgeBlobs(self):

        if self.rect_item is None:
            return

        # Get the bounding rect of the rectangle and its position
        rect = self.rect_item.boundingRect()
        rect.moveTopLeft(self.rect_item.pos())
        rect = rect.normalized()

        print(f"rect coordinates are {rect.left(), rect.right(), rect.top(), rect.bottom()}")


        #QUIRINO :  margin to consider the edge TO FINETUNE!!!!
        margin = 10  

        
        # filtered_blobs = []
        blobs = self.created_blobs.copy()
        for blob in blobs:
            
            # Create a QRectF for each blob's bounding box
            #bbox = QRectF(blob.bbox[0], blob.bbox[1], blob.bbox[2], blob.bbox[3])
            # print(f"bbox coordinates are {bbox.top(), bbox.bottom(), bbox.left(), bbox.right()}")
            #BOUNDING BOX ORDER: WHY?
            #rect left  right   top bottom
            #bbox top   bottom  left  right
           
            #use directly the blob bounding box instead of QRectF
            bbox = blob.bbox
            top = bbox[0]
            left = bbox[1]
            right = bbox[1] + bbox[2]
            bottom = bbox[0] + bbox[3]
            
            #remove blobs if they are on the edges
            if  (
                left - rect.left() < margin or
                rect.right() - right < margin or
                top - rect.top() < margin or
                rect.bottom() - bottom < margin
            ):
                self.created_blobs.remove(blob)    

    def reset(self):

        torch.cuda.empty_cache()
        if self.sam_net is not None:
            del self.sam_net
            self.sam_net = None
        #     #self.viewerplus.resetTools()
        #     ##self.resetWorkArea()

    def setSize(self, delta):
        #increase value got from delta angle of mouse wheel
        increase = float(delta.y()) / 10.0
        
        #set increase or decrease value on  wheel rotation direction
        if 0.0 < increase < 1.0:
            increase = 100
        elif -1.0 < increase < 0.0:
            increase = -100

        #rescale rect_item on zoom factor from wheel event
        # added *2 to mantain rectangle inside the map
        new_width = self.width + (increase)
        new_height = self.height + (increase)
        
        #limit the rectangle to 512x512 for SAM segmentation
        if new_width < 512 or new_height < 512:
            new_width = 512
            new_height = 512

        # limit the rectangle to 2048x2048 for SAM segmentation
        if new_width > 2048 or new_height > 2048:
            new_width = 2048
            new_height = 2048
  
        # print(f"rect_item width and height are {new_width, new_height}")
        if self.rect_item is not None:
            self.rect_item.setRect(0, 0, new_width, new_height)

        self.width = new_width
        self.height = new_height
        
    def handlemouseMove(self, x, y):
        # print(f"Mouse moved to ({x}, {y})")
        if self.rect_item is not None:
            # self.center_item.setPos(x, y)
            self.rect_item.setPos(x- self.width//2, y - self.height//2)
            
    #SAM segmentation on space key pressed instead of left mouse button pressed
    # def leftPressed(self, x, y, mods):
    def apply(self):
        
        sam_seed = self.width//30
        print(f"number of sam_seed is {sam_seed}")

        # Crop the part of the map inside the self.rect_item area
        rect = self.rect_item.boundingRect()
        rect.moveTopLeft(self.rect_item.pos())
        rect = rect.normalized()
        rect = rect.intersected(self.viewerplus.sceneRect())
        cropped_image = self.viewerplus.img_map.copy(rect.toRect())

        offset = self.rect_item.pos()
        self.offset = [offset.x(), offset.y()]

        # Perform segmentation on the cropped image
        self.segment(cropped_image, sam_seed)

    def segment(self, image, seed = 32, save_status=True):

        self.infoMessage.emit("Segmentation is ongoing..")
        self.log.emit("[TOOL][SAM] Segmentation begins..")

        QApplication.setOverrideCursor(Qt.WaitCursor)
        if not self.loadNetwork():
            return

        QApplication.restoreOverrideCursor()

        mask_generator = SamAutomaticMaskGenerator(
            model=self.sam_net,

            # points_per_side = seed,
            points_per_side= 32,
            
            points_per_batch=64,
            crop_n_layers = 0,
            pred_iou_thresh = 0.88,
            stability_score_thresh=  0.95,
            stability_score_offset = 1.0,
            box_nms_thresh = 0.7,
            crop_nms_thresh = 0.7,
            min_mask_region_area = 1000,
            crop_overlap_ratio = 0.34333,
            crop_n_points_downscale_factor = 1,
            output_mode = "binary_mask"
        )

        image = genutils.qimageToNumpyArray(image)
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

        start = time.time()

        masks = mask_generator.generate(image)

        end = time.time()

        print(end-start)

        offx = self.offset[0]
        offy = self.offset[1]
        for mask in masks:
            bbox = mask["bbox"]
            bbox = [int(value) for value in bbox]
            segm_mask = mask["segmentation"].astype('uint8')*255
            segm_mask_crop = segm_mask[bbox[1]:bbox[1]+bbox[3], bbox[0]:bbox[0]+bbox[2]]
            blob = self.viewerplus.image.annotations.createBlobFromSingleMask(segm_mask_crop, bbox[0] + offx, bbox[1] + offy)
            self.created_blobs.append(blob)

        print(f"self.created_blob len pre is {len(self.created_blobs)}")
        
        # self.removeOverlappingBlobs(self.created_blobs)
        # self.removeOverlapping(self.created_blobs)
        genutils.removeOverlapping(self.created_blobs, self.created_blobs)
        
        print(f"self.created_blob len post is {len(self.created_blobs)}")
        
        self.removeEdgeBlobs()

        print(f"self.created_blob len post removeEdge is {len(self.created_blobs)}")
        

        print(f"Number of yet annotated blobs is {len(self.viewerplus.image.annotations.seg_blobs)}")

        # self.removeAnnotatedBlobs()
        # self.removeOverlapping(self.viewerplus.image.annotations.seg_blobs, annotated = True)
        genutils.removeOverlapping(self.created_blobs, self.viewerplus.image.annotations.seg_blobs, annotated = True)

        print(f"self.created_blob len post annotated is {len(self.created_blobs)}")
            
        for blob in self.created_blobs:
            self.viewerplus.addBlob(blob, selected=True)
        
        self.created_blobs = []

              
        self.samEnded.emit()

    
    #method to display the rectangle on the map
    def enable(self, enable = False):
        if enable == True:
            self.rect_item = self.viewerplus.scene.addRect(0, 0, self.width, self.height, QPen(Qt.black, 5, Qt.DotLine)) 
        else:
            if self.rect_item is not None:
                self.viewerplus.scene.removeItem(self.rect_item)
            self.rect_item = None
            # self.center_item.setVisible(False)
       
       
    #method to emit the message for the tool     
    # def toolMessage(self):
    #     self.tool_message.emit("To resize windows cursor: \
    #     Shift + Mouse Wheel  \
    #     \
    #     To apply SAM segmentation: \
    #     Spacebar")

    #
    #
    # def drawBlobs(self):
    #
    #     for blob in self.created_blobs:
    #         self.viewerplus.addBlob(blob, selected=False)



            # # if it has just been created remove the current graphics item in order to set it again
            # if blob.qpath_gitem is not None:
            #     scene.removeItem(blob.qpath_gitem)
            #     del blob.qpath_gitem
            #     blob.qpath_gitem = None
            #
            # # custom drawing for created blobs
            #
            # blob.setupForDrawing()
            # pen = QPen(Qt.white)
            # pen.setWidth(2)
            # pen.setCosmetic(True)
            # brush = QBrush(Qt.SolidPattern)
            # brush.setColor(Qt.white)
            # brush.setStyle(Qt.Dense4Pattern)
            # blob.qpath_gitem = scene.addPath(blob.qpath, pen, brush)
            # blob.qpath_gitem.setZValue(1)
            # blob.qpath_gitem.setOpacity(self.viewerplus.transparency_value)
