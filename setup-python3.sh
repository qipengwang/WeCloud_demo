#!/bin/zsh
conda create -n chinese-ocr3 python=3.6  pip scipy numpy Pillow jupyter -y
conda activate chinese-ocr3
pip install tensorflow==1.7.0 easydict futures==3.1.1 h5py==2.10.0 lmdb mahotas keras==2.1.6 Cython opencv-python Pillow torch torchaudio torchvision
cd ./ctpn/lib/utils
./make-for-cpu.sh
cd -

conda create -n classify python==3.8 -y
conda activate classify
pip install torch==1.12.0 torchvision==0.13.0 torchaudio==0.12.0
