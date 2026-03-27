import cv2
from os.path import join as pjoin
import time
import json
import numpy as np

import detect_compo.lib_ip.ip_preprocessing as pre
import detect_compo.lib_ip.ip_draw as draw
import detect_compo.lib_ip.ip_detection as det
import detect_compo.lib_ip.file_utils as file
import detect_compo.lib_ip.Component as Compo
from config.CONFIG_UIED import Config
C = Config()


def nesting_inspection(org, grey, compos, ffl_block):
    '''
    Inspect all big compos through block division by flood-fill
    :param ffl_block: gradient threshold for flood-fill
    :return: nesting compos
    '''
    nesting_compos = []
    for i, compo in enumerate(compos):
        if compo.height > 50:
            replace = False
            clip_grey = compo.compo_clipping(grey)
            n_compos = det.nested_components_detection(clip_grey, org, grad_thresh=ffl_block, show=False)
            Compo.cvt_compos_relative_pos(n_compos, compo.bbox.col_min, compo.bbox.row_min)

            for n_compo in n_compos:
                if n_compo.redundant:
                    compos[i] = n_compo
                    replace = True
                    break
            if not replace:
                nesting_compos += n_compos
    return nesting_compos



def read_img_from_array(image, resize_height=None, kernel_size=None):

    def resize_by_height(org):
        w_h_ratio = org.shape[1] / org.shape[0]
        resize_w = resize_height * w_h_ratio
        return cv2.resize(org, (int(resize_w), int(resize_height)))

    try:
        img = image.copy()
        if kernel_size is not None:
            img = cv2.medianBlur(img, kernel_size)
        if resize_height is not None:
            img = resize_by_height(img)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return img, gray

    except Exception as e:
        print(e)
        print("*** Img Processing Failed ***")
        return None, None

def compos_to_json(compos):

    output = {"compos": []}

    for compo in compos:

        # bbox
        column_min, row_min, column_max, row_max = compo.put_bbox()

        compo_json = {
            "id": compo.id,
            "class": compo.category,
            "column_min": column_min,
            "row_min": row_min,
            "column_max": column_max,
            "row_max": row_max,
            "width": compo.width,
            "height": compo.height
        }

        output["compos"].append(compo_json)

    return output

# def compo_detection(input_img_path, output_root, uied_params,
#                     resize_by_height=800, classifier=None, show=False, wai_key=0):

#     # start = time.clock()
#     start = time.perf_counter()
#     name = input_img_path.split('/')[-1][:-4] if '/' in input_img_path else input_img_path.split('\\')[-1][:-4]
#     ip_root = file.build_directory(pjoin(output_root, "ip"))

#     # *** Step 1 *** pre-processing: read img -> get binary map
#     org, grey = pre.read_img(input_img_path, resize_by_height)
#     binary = pre.binarization(org, grad_min=int(uied_params['min-grad']))

#     # *** Step 2 *** element detection
#     det.rm_line(binary, show=show, wait_key=wai_key)
#     uicompos = det.component_detection(binary, min_obj_area=int(uied_params['min-ele-area']))

#     # *** Step 3 *** results refinement
#     uicompos = det.compo_filter(uicompos, min_area=int(uied_params['min-ele-area']), img_shape=binary.shape)
#     uicompos = det.merge_intersected_compos(uicompos)
#     det.compo_block_recognition(binary, uicompos)
#     if uied_params['merge-contained-ele']:
#         uicompos = det.rm_contained_compos_not_in_block(uicompos)
#     Compo.compos_update(uicompos, org.shape)
#     Compo.compos_containment(uicompos)

#     # *** Step 4 ** nesting inspection: check if big compos have nesting element
#     uicompos += nesting_inspection(org, grey, uicompos, ffl_block=uied_params['ffl-block'])
#     Compo.compos_update(uicompos, org.shape)
#     draw.draw_bounding_box(org, uicompos, show=show, name='merged compo', write_path=pjoin(ip_root, name + '.jpg'), wait_key=wai_key)

#     # *** Step 7 *** save detection result
#     Compo.compos_update(uicompos, org.shape)
#     file.save_corners_json(pjoin(ip_root, name + '.json'), uicompos)
#     print("[Compo Detection Completed in %.3f s] Input: %s Output: %s" % (time.perf_counter() - start, input_img_path, pjoin(ip_root, name + '.json')))

def compo_detection(image, uied_params, resize_by_height=800):

    # *** Step 1 *** pre-processing: read img -> get binary map
    org, grey = read_img_from_array(image, resize_height=resize_by_height)
    binary = pre.binarization(org, grad_min=int(uied_params['min-grad']))

    # *** Step 2 *** element detection
    det.rm_line(binary, show=False, wait_key=0)
    uicompos = det.component_detection(binary, min_obj_area=int(uied_params['min-ele-area']))

    # *** Step 3 *** results refinement
    uicompos = det.compo_filter(uicompos, min_area=int(uied_params['min-ele-area']), img_shape=binary.shape)
    uicompos = det.merge_intersected_compos(uicompos)
    det.compo_block_recognition(binary, uicompos)
    if uied_params['merge-contained-ele']:
        uicompos = det.rm_contained_compos_not_in_block(uicompos)
    Compo.compos_update(uicompos, org.shape)
    Compo.compos_containment(uicompos)

    # *** Step 4 ** nesting inspection: check if big compos have nesting element
    uicompos += nesting_inspection(org, grey, uicompos, ffl_block=uied_params['ffl-block'])
    Compo.compos_update(uicompos, org.shape)
    result = compos_to_json(uicompos)

    return result