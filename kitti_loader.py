#!/usr/bin/env python
# -*- coding:UTF-8 -*-

# File Name : kitti_loader.py
# Purpose :
# Creation Date : 09-12-2017
# Last Modified : 2017年12月12日 星期二 15时11分08秒
# Created By : Jeasine Ma [jeasinema[at]gmail[dot]com]

import cv2
import numpy as np
import os
import sys
import glob
import threading
import time
import math
import random
from sklearn.utils import shuffle
from multiprocessing import Lock, Process, Queue as Queue, Value, Array, cpu_count

from config import cfg

# for non-raw dataset
class KittiLoader(object):

    # return: 
    # tag (N)
    # label (N) (N') (just raw string in the label files) (when it is not test set)
    # rgb (N, H, W, C)
    # raw_lidar (N) (N', 4)
    # vox_feature 
    # vox_number 
    # vox_coordinate 

    def __init__(self, object_dir='.', queue_size=20, require_shuffle=False, is_testset=True, batch_size=1, use_multi_process_num=0, split_file=''):
        assert(use_multi_process_num >= 0)
        self.object_dir = object_dir
        self.is_testset = is_testset
        self.use_multi_process_num = use_multi_process_num if not self.is_testset else 1
        self.require_shuffle = require_shuffle if not self.is_testset else False
        self.batch_size=batch_size if not self.is_testset else 1
        self.split_file = split_file 

        if self.split_file != '':
            # use split file  
            _tag = []
            # self.f_rgb, self.f_lidar, self.f_label = [], [], []
            self.f_voxel, self.f_label = [], [], []
            for line in open(self.split_file, 'r').readlines():
                line = line[:-1] # remove '\n'
                _tag.append(line)
                self.f_rgb.append(os.path.join(self.object_dir, 'image_2', line+'.png'))
                self.f_lidar.append(os.path.join(self.object_dir, 'velodyne', line+'.bin'))
                self.f_voxel.append(os.path.join(self.object_dir, 'voxel' if cfg.DETECT_OBJ == 'Car' else 'voxel_ped', line+'.npz'))
                self.f_label.append(os.path.join(self.object_dir, 'label_2', line+'.txt'))
        else:
            self.f_rgb = glob.glob(os.path.join(self.object_dir, 'image_2', '*.png'))
            self.f_rgb.sort()
            self.f_lidar = glob.glob(os.path.join(self.object_dir, 'velodyne', '*.bin'))
            self.f_lidar.sort()
            self.f_label = glob.glob(os.path.join(self.object_dir, 'label_2', '*.txt'))
            self.f_label.sort()
            self.f_voxel = glob.glob(os.path.join(self.object_dir, 'voxel' if cfg.DETECT_OBJ == 'Car' else 'voxel_ped', '*.npz'))
            self.f_voxel.sort()


        self.data_tag =  [name.split('/')[-1].split('.')[-2] for name in self.f_label]
        # assert(len(self.f_rgb) == len(self.f_lidar) == len(self.f_label) == len(self.data_tag))
        assert(len(self.f_voxel) == len(self.f_label) == len(self.data_tag) == len(self.f_rgb) == len(self.f_lidar))
        self.dataset_size = len(self.f_label)
        self.already_extract_data = 0
        self.cur_frame_info = ''

        print("Dataset total length: {}".format(len(self.f_label)))
        if self.require_shuffle:
            self.shuffle_dataset()

        self.queue_size = queue_size
        self.require_shuffle = require_shuffle
        self.dataset_queue = Queue()  # must use the queue provided by multiprocessing module(only this can be shared)

        self.load_index = 0
        if self.use_multi_process_num == 0:
            self.loader_worker = [threading.Thread(target=self.loader_worker_main, args=(self.batch_size,))]
        else:
            self.loader_worker = [Process(target=self.loader_worker_main, args=(self.batch_size,)) for i in range(self.use_multi_process_num)]
        self.work_exit = Value('i', 0)
        [i.start() for i in self.loader_worker]

        # This operation is not thread-safe
        self.rgb_shape = (cfg.IMAGE_HEIGHT, cfg.IMAGE_WIDTH, 3)
    
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.work_exit.value = True

    def __len__(self):
        return self.dataset_size

    def fill_queue(self, batch_size=0):
        load_index = self.load_index
        self.load_index += batch_size 
        if self.load_index >= self.dataset_size:
            if not self.is_testset:  # test set just end
                if self.require_shuffle:
                    self.shuffle_dataset()
                load_index = 0
                self.load_index = load_index + batch_size 
            else:
                self.work_exit.value = True

        labels, tag, voxel, rgb, raw_lidar = [], [], [], [], []
        for _ in range(batch_size):
            try:
                rgb.append(cv2.resize(cv2.imread(self.f_rgb[load_index]), (cfg.IMAGE_WIDTH, cfg.IMAGE_HEIGHT)))
                raw_lidar.append(np.fromfile(self.f_lidar[load_index], dtype=np.float32).reshape((-1, 4)))
                labels.append([line for line in open(self.f_label[load_index], 'r').readlines()])
                tag.append(self.data_tag[load_index])
                voxel.append(np.load(self.f_voxel[load_index]))

                load_index += 1
            except:
                if not self.is_testset:  # test set just end
                    self.load_index = 0
                    if self.require_shuffle:
                        self.shuffle_dataset()
                else:
                    self.work_exit.value = True
        
        _, vox_feature, vox_number, vox_coordinate = build_input(voxel)
        self.dataset_queue.put_nowait((labels, (vox_feature, vox_number, vox_coordinate), rgb, raw_lidar, tag))

    def load(self):
        try: 
            if self.is_testset and self.already_extract_data >= self.dataset_size:
                return None
            
            buff = self.dataset_queue.get()
            label = buff[0]
            vox_feature = buff[1][0]
            vox_number = buff[1][1]
            vox_coordinate = buff[1][2]
            rgb = buff[2]
            raw_lidar = buff[3]
            tag = buff[4]
            self.cur_frame_info = buff[4]

            self.already_extract_data += self.batch_size

            if self.is_testset:
                ret = (
                    np.array(tag),
                    np.array(label),
                    np.array(vox_feature), 
                    np.array(vox_number), 
                    np.array(vox_coordinate), 
                    np.array(rgb),
                    np.array(raw_lidar)

                )
            else:
                ret = (
                    np.array(tag),
                    np.array(label), 
                    np.array(vox_feature),
                    np.array(vox_number),
                    np.array(vox_coordinate),
                    np.array(rgb),
                    np.array(raw_lidar)
                )
        except:
            print("Dataset empty!")
            ret = None
        return ret

    def load_specified(self, index=0):
        rgb = cv2.resize(cv2.imread(self.f_rgb[index]), (cfg.IMAGE_WIDTH, cfg.IMAGE_HEIGHT))
        raw_lidar = np.fromfile(self.f_lidar[index], dtype=np.float32).reshape((-1, 4))
        labels = [line for line in open(self.f_label[index], 'r').readlines()]
        tag = self.data_tag[index]
        
        if self.is_testset:
            ret = (
                np.array([tag]),
                np.array([rgb]), 
                np.array([raw_lidar]),
            )
        else:
            ret = (
                np.array([tag]),
                np.array([labels]), 
                np.array([rgb]),
                np.array([raw_lidar]),
            )
        return ret


    def loader_worker_main(self, batch_size):
        if self.require_shuffle:
            self.shuffle_dataset()
        while not self.work_exit.value:
            if self.dataset_queue.qsize() >= self.queue_size // 2:
                time.sleep(1)
            else:
                self.fill_queue(batch_size)  # since we use multiprocessing, 1 is ok

    def get_shape(self):
        return self.rgb_shape

    def shuffle_dataset(self):
        # to prevent diff loader load same data
        index = shuffle([i for i in range(len(self.f_label))], random_state=random.randint(0, self.use_multi_process_num**5))
        self.f_label = [self.f_label[i] for i in index]
        self.f_rgb = [self.f_rgb[i] for i in index]
        self.f_lidar = [self.f_lidar[i] for i in index]
        self.f_voxel = [self.f_voxel[i] for i in index]
        self.data_tag = [self.data_tag[i] for i in index]

    def get_frame_info(self):
        return self.cur_frame_info


def build_input(voxel_dict_list):
    batch_size = len(voxel_dict_list)

    feature_list = []
    number_list = []
    coordinate_list = []
    for i, voxel_dict in zip(range(batch_size), voxel_dict_list):
        feature_list.append(voxel_dict['feature_buffer'])
        number_list.append(voxel_dict['number_buffer'])
        coordinate = voxel_dict['coordinate_buffer']
        coordinate_list.append(
            np.pad(coordinate, ((0, 0), (1, 0)),
                   mode='constant', constant_values=i))

    feature = np.concatenate(feature_list)
    number = np.concatenate(number_list)
    coordinate = np.concatenate(coordinate_list)
    return batch_size, feature, number, coordinate



if __name__ == '__main__':
    pass
