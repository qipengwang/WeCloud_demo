# coding:utf-8
import time
from glob import glob

import numpy as np
from PIL import Image

import model
# ces

paths = glob('./test/*.*')

def ocr_demo(imagepath):
    im = Image.open(imagepath)
    img = np.array(im.convert('RGB'))
    t = time.time()
    '''
    result,img,angel分别对应-识别结果，图像的数组，文字旋转角度
    '''
    result, img, angle = model.model(
        img, model='keras', adjust=True, detectAngle=True)
    print("It takes time:{}s".format(time.time() - t))
    print("---------------------------------------")
    res = []
    for key in result:
        res.append(result[key][1])
        print(result[key][1])
    return res

if __name__ == "__main__":
    ocr_demo("test/3.png")