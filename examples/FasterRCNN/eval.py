#!/usr/bin/env python
# -*- coding: utf-8 -*-
# File: eval.py

import tqdm
import os
from collections import namedtuple
import numpy as np
import cv2

from tensorpack.utils.utils import get_tqdm_kwargs

from pycocotools.coco import COCO
from pycocotools.cocoeval import COCOeval
import pycocotools.mask as cocomask

from coco import COCOMeta
from common import CustomResize
import config

DetectionResult = namedtuple(
    'DetectionResult',
    ['box', 'score', 'class_id', 'mask'])
"""
class_id: int, 1~NUM_CLASS
box: 4 float
score: float
"""


def fill_full_mask(box, mask, shape):
    """
    Args:
        box: 4 float
        mask: 14x14 floats
        shape: h,w
    """
    # int() is floor
    # box fpcoor=0.0 -> intcoor=0.0
    x0, y0 = list(map(int, box[:2] + 0.5))
    # box fpcoor=h -> intcoor=h-1, inclusive
    x1, y1 = list(map(int, box[2:] - 0.5))    # inclusive
    x1 = max(x0, x1)    # require at least 1x1
    y1 = max(y0, y1)

    w = x1 + 1 - x0
    h = y1 + 1 - y0

    # rounding errors could happen here, masks were not originally computed from integer coords
    mask = (cv2.resize(mask, (w, h)) > 0.5).astype('uint8')
    ret = np.zeros(shape, dtype='uint8')
    ret[y0:y1 + 1, x0:x1 + 1] = mask
    return ret


def detect_one_image(img, model_func):
    """
    Run detection on one image, using the TF callable.
    This function should handle the preprocessing internally.

    Args:
        img: an image
        model_func: a callable from TF model, takes image and returns (probs, boxes)

    Returns:
        [DetectionResult]
    """

    orig_shape = img.shape[:2]
    resizer = CustomResize(config.SHORT_EDGE_SIZE, config.MAX_SIZE)
    resized_img = resizer.augment(img)
    scale = (resized_img.shape[0] * 1.0 / img.shape[0] + resized_img.shape[1] * 1.0 / img.shape[1]) / 2
    ret = model_func(resized_img)
    # boxes, probs, labels [, masks]
    ret[0] = ret[0] / scale

    if config.MODE.mask:
        full_masks = [fill_full_mask(box, mask, orig_shape)
                      for box, mask in zip(ret[0], ret[3])]
        ret[3] = full_masks

    results = [DetectionResult(*args) for args in zip(*ret)]
    return results


def eval_on_dataflow(df, detect_func):
    """
    Args:
        df: a DataFlow which produces (image, image_id)
        detect_func: a callable, takes [image] and returns [DetectionResult]

    Returns:
        list of dict, to be dumped to COCO json format
    """
    df.reset_state()
    all_results = []
    with tqdm.tqdm(total=df.size(), **get_tqdm_kwargs()) as pbar:
        for img, img_id in df.get_data():
            results = detect_func(img)
            for r in results:
                box = r.box
                cat_id = COCOMeta.class_id_to_category_id[r.class_id]
                box[2] -= box[0]
                box[3] -= box[1]

                mask = r.mask
                rle = cocomask.encode(
                    np.array(mask[:, :, None], order='F'))[0]
                rle['counts'] = rle['counts'].decode('ascii')
                all_results.append({
                    'image_id': img_id,
                    'category_id': cat_id,
                    'bbox': list(map(lambda x: float(round(x, 1)), box)),
                    'score': float(round(r.score, 2)),
                    'segmentation': rle
                })
            pbar.update(1)
    return all_results


# https://github.com/pdollar/coco/blob/master/PythonAPI/pycocoEvalDemo.ipynb
def print_evaluation_scores(json_file):
    assert config.BASEDIR and os.path.isdir(config.BASEDIR)
    annofile = os.path.join(
        config.BASEDIR, 'annotations',
        'instances_{}.json'.format(config.VAL_DATASET))
    coco = COCO(annofile)
    cocoDt = coco.loadRes(json_file)
    cocoEval = COCOeval(coco, cocoDt, 'bbox')
    cocoEval.evaluate()
    cocoEval.accumulate()
    cocoEval.summarize()

    if config.MODE.mask:
        cocoEval = COCOeval(coco, cocoDt, 'segm')
        cocoEval.evaluate()
        cocoEval.accumulate()
        cocoEval.summarize()
