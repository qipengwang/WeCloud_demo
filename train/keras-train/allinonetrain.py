# -*- coding: utf-8 -*-
import random
import sys

import lmdb
import numpy as np
import six
import torch
import torchvision.transforms as transforms
from PIL import Image
from keras.layers import Flatten, BatchNormalization, Permute, TimeDistributed, Dense, Bidirectional, GRU
from keras.layers import Input, Conv2D, MaxPooling2D, ZeroPadding2D
from keras.models import Model
from torch.utils.data import Dataset
from torch.utils.data import sampler

rnnunit = 256
from keras import backend as K

from keras.layers import Lambda
from keras.optimizers import SGD


class lmdbDataset(Dataset):
    def __init__(self, root=None, transform=None, target_transform=None):
        self.env = lmdb.open(
            root,
            max_readers=1,
            readonly=True,
            lock=False,
            readahead=False,
            meminit=False)

        if not self.env:
            print('cannot creat lmdb from %s' % (root))
            sys.exit(0)

        with self.env.begin(write=False) as txn:
            nSamples = int(txn.get('num-samples'.encode()))
            print("nSamples:{}".format(nSamples))
            self.nSamples = nSamples

        self.transform = transform
        self.target_transform = target_transform

    def __len__(self):
        return self.nSamples

    def __getitem__(self, index):
        assert index <= len(self), 'index range error'
        index += 1
        with self.env.begin(write=False) as txn:
            img_key = 'image-%09d' % index
            imgbuf = txn.get(img_key.encode())

            buf = six.BytesIO()
            buf.write(imgbuf)
            buf.seek(0)
            try:
                img = Image.open(buf).convert('L')
                # img.save("1111111111.jpg")
            except IOError:
                print('Corrupted image for %d' % index)
                if index > self.nSamples - 1:
                    index = 0
                return self[index + 1]

            if self.transform is not None:
                img = self.transform(img)

            label_key = 'label-%09d' % index
            label = str(txn.get(label_key.encode()), 'utf-8')

            if self.target_transform is not None:
                label = self.target_transform(label)
        # print(img,label)
        return (img, label)


class resizeNormalize(object):
    def __init__(self, size, interpolation=Image.BILINEAR):
        self.size = size
        self.interpolation = interpolation
        self.toTensor = transforms.ToTensor()

    def __call__(self, img):
        img = img.resize(self.size, self.interpolation)
        img = self.toTensor(img)
        img.sub_(0.5).div_(0.5)
        return img


class randomSequentialSampler(sampler.Sampler):
    def __init__(self, data_source, batch_size):
        self.num_samples = len(data_source)
        self.batch_size = batch_size

    def __iter__(self):
        n_batch = len(self) // self.batch_size
        tail = len(self) % self.batch_size
        index = torch.LongTensor(len(self)).fill_(0)
        for i in range(n_batch):
            random_start = random.randint(0, len(self) - self.batch_size)
            batch_index = random_start + torch.range(0, self.batch_size - 1)
            index[i * self.batch_size:(i + 1) * self.batch_size] = batch_index
        # deal with tail
        if tail:
            random_start = random.randint(0, len(self) - self.batch_size)
            tail_index = random_start + torch.range(0, tail - 1)
            index[(i + 1) * self.batch_size:] = tail_index

        return iter(index)

    def __len__(self):
        return self.num_samples


class alignCollate(object):
    def __init__(self, imgH=32, imgW=100, keep_ratio=False, min_ratio=1):
        self.imgH = imgH
        self.imgW = imgW
        self.keep_ratio = keep_ratio
        self.min_ratio = min_ratio

    def __call__(self, batch):
        images, labels = zip(*batch)

        imgH = self.imgH
        imgW = self.imgW
        if self.keep_ratio:
            ratios = []
            for image in images:
                w, h = image.size
                ratios.append(w / float(h))
            ratios.sort()
            max_ratio = ratios[-1]
            imgW = int(np.floor(max_ratio * imgH))
            imgW = max(imgH * self.min_ratio, imgW)  # assure imgH >= imgW

        transform = resizeNormalize((imgW, imgH))
        images = [transform(image) for image in images]
        images = torch.cat([t.unsqueeze(0) for t in images], 0)

        return images, labels


def ctc_lambda_func(args):
    y_pred, labels, input_length, label_length = args
    # print("cccccccccc:",y_pred,labels,input_length,label_length)
    y_pred = y_pred[:, 2:, :]

    return K.ctc_batch_cost(labels, y_pred, input_length, label_length)


def get_model(height, nclass):
    input = Input(shape=(height, None, 1), name='the_input')
    m = Conv2D(64, kernel_size=(3, 3), activation='relu', padding='same', name='conv1')(input)
    m = MaxPooling2D(pool_size=(2, 2), strides=(2, 2), name='pool1')(m)
    m = Conv2D(128, kernel_size=(3, 3), activation='relu', padding='same', name='conv2')(m)
    m = MaxPooling2D(pool_size=(2, 2), strides=(2, 2), name='pool2')(m)
    m = Conv2D(256, kernel_size=(3, 3), activation='relu', padding='same', name='conv3')(m)
    m = Conv2D(256, kernel_size=(3, 3), activation='relu', padding='same', name='conv4')(m)

    m = ZeroPadding2D(padding=(0, 1))(m)
    m = MaxPooling2D(pool_size=(2, 2), strides=(2, 1), padding='valid', name='pool3')(m)

    m = Conv2D(512, kernel_size=(3, 3), activation='relu', padding='same', name='conv5')(m)
    m = BatchNormalization(axis=1)(m)
    m = Conv2D(512, kernel_size=(3, 3), activation='relu', padding='same', name='conv6')(m)
    m = BatchNormalization(axis=1)(m)
    m = ZeroPadding2D(padding=(0, 1))(m)
    m = MaxPooling2D(pool_size=(2, 2), strides=(2, 1), padding='valid', name='pool4')(m)
    m = Conv2D(512, kernel_size=(2, 2), activation='relu', padding='valid', name='conv7')(m)

    m = Permute((2, 1, 3), name='permute')(m)
    m = TimeDistributed(Flatten(), name='timedistrib')(m)

    m = Bidirectional(GRU(rnnunit, return_sequences=True), name='blstm1')(m)
    m = Dense(rnnunit, name='blstm1_out', activation='linear')(m)
    m = Bidirectional(GRU(rnnunit, return_sequences=True), name='blstm2')(m)
    y_pred = Dense(nclass, name='blstm2_out', activation='softmax')(m)

    basemodel = Model(inputs=input, outputs=y_pred)

    labels = Input(name='the_labels', shape=[None, ], dtype='float32')
    input_length = Input(name='input_length', shape=[1], dtype='int64')
    label_length = Input(name='label_length', shape=[1], dtype='int64')

    loss_out = Lambda(ctc_lambda_func, output_shape=(1,), name='ctc')([y_pred, labels, input_length, label_length])
    model = Model(inputs=[input, labels, input_length, label_length], outputs=[loss_out])
    sgd = SGD(lr=0.001, decay=1e-6, momentum=0.9, nesterov=True, clipnorm=5)
    # model.compile(loss={'ctc': lambda y_true, y_pred: y_pred}, optimizer='adadelta')
    model.compile(loss={'ctc': lambda y_true, y_pred: y_pred}, optimizer=sgd)
    model.summary()
    return model, basemodel


alphabet = """'?????????????????????????????????????????????????????????????????????208-7???>:],??????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????13?????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????!??????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????|;??????????????????????????????????????????????????????????????????H????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????.????????????????????????????????????????????????????????????/*??????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????54????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????(?????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????6?????????????????????????????????????????????????????????????????????????????????????????????)?????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????su??????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????[?????????9?????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????SYFDA????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????P??????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????T??????????????????????????????????????????|???????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????@???????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????%??????????????????????????????"?????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????r??????????????????????????????????????????????????????????????????????????????????????????=?????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????q?????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????J????????????????????????????????????l?????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????&??????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????x??????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????z????????????????????????????????????????????et??????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????b??????????????????????????????????????????????????????????????????????????????<?????????????????????????????????????????????L??????????????????????????????????????????????????????????????????????????????????????????wih???????????????????????????????????????????????????????????????????????????????????????????????????+???????????????????????????????????????IBN????????????????????????????????????????????????????????????????????????????????????????????????????????????^_?????????????????????????????????M??????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????}???????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????~?????????????????????Z?????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????Co?????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????E?????????????????????f??????????????????????????????????????????????????????????????????????????????????????????????????????????????????\????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????U??????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????apyng??????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????Q???????????????????????????O?????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????k?????????????????????????????????????????????????????????????????????????????????????????????????????????$????????????????????????????????????????????????c?????????????????????????????????????????????????????????v??????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????W??????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????RG?????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????d??????????????????????????????????????????????????????????????K??????????????????X????????????????????????????????????????????????????????????????????????????????????????????????m???????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????`???????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????V?????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????#?????????????????????????????????????????????????????????????????????????????????{?????????j????????????????????????????????????????????????????????????????????????????????????????????????????????? """
characters = alphabet[:]

nclass = len(characters) + 1

trainroot = '../data/lmdb/train'
valroot = '../data/lmdb/val'
batchSize = 32
workers = 4
imgH = 32
imgW = 256
keep_ratio = False
random_sample = False


def one_hot(text, length=10, characters=characters):
    label = np.zeros(length)
    # print(type(text))
    for i, char in enumerate(text):
        index = characters.find(char)
        if index == -1:
            index = characters.find(u' ')
        # print(i,char,length)
        # if i < length:
        label[i] = index
    return label


n_len = 10


def gen(loader, flag='train'):
    while True:
        i = 0
        n = len(loader)
        for X, Y in loader:
            X = X.numpy()
            X = X.reshape((-1, imgH, imgW, 1))
            if flag == 'test':
                Y = Y.numpy()

            Y = np.array(Y)
            Length = int(imgW / 4) - 1
            batchs = X.shape[0]
            # Y = Y.numpy()
            if i > n - 1:
                i = 0
                break

            yield [X, Y, np.ones(batchs) * int(Length), np.ones(batchs) * n_len], np.ones(batchs)


sampler = None
train_dataset = lmdbDataset(root=trainroot, target_transform=one_hot)

train_loader = torch.utils.data.DataLoader(
    train_dataset, batch_size=batchSize,
    shuffle=True, sampler=sampler,
    num_workers=int(workers),
    collate_fn=alignCollate(imgH=imgH, imgW=imgW, keep_ratio=keep_ratio))

test_dataset = lmdbDataset(
    root=valroot, transform=resizeNormalize((imgW, imgH)), target_transform=one_hot)

test_loader = torch.utils.data.DataLoader(
    test_dataset, shuffle=True, batch_size=batchSize, num_workers=int(workers))

if __name__ == '__main__':
    from keras.callbacks import ModelCheckpoint, ReduceLROnPlateau

    model, basemodel = get_model(height=imgH, nclass=nclass)
    import os

    if os.path.exists(f'{os.getcwd()}/crnn_ocr/pretrain-models/keras.hdf5'):
        basemodel.load_weights(f'{os.getcwd()}/crnn_ocr/pretrain-models/keras.hdf5')

    ##????????????????????????model?????????
    checkpointer = ModelCheckpoint(filepath="save_model/model{epoch:02d}-{val_loss:.4f}.hdf5", monitor='val_loss',
                                   verbose=0, save_weights_only=False, save_best_only=True)
    rlu = ReduceLROnPlateau(monitor='val_loss', factor=0.1, patience=1, verbose=0, mode='auto', epsilon=0.0001,
                            cooldown=0, min_lr=0)

    model.fit_generator(gen(train_loader, flag='train'),
                        steps_per_epoch=102400,
                        epochs=200,
                        validation_data=gen(test_loader, flag='test'),
                        callbacks=[checkpointer, rlu],
                        validation_steps=1024)
