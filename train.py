#!/usr/bin/env python
# -*- coding:UTF-8 -*-

# File Name : train.py
# Purpose :
# Creation Date : 09-12-2017
# Last Modified : 2017年12月12日 星期二 15时22分46秒
# Created By : Jeasine Ma [jeasinema[at]gmail[dot]com]

import glob
import argparse
import os
import time
import tensorflow as tf
from itertools import count

from config import cfg
from model import RPN3D
from kitti_loader import KittiLoader


parser = argparse.ArgumentParser(description='training')
parser.add_argument('-i', '--max-epoch', type=int, nargs='?', default=10,
                    help='max epoch')
parser.add_argument('-n', '--tag', type=str, nargs='?', default='default',
                    help='set log tag')
parser.add_argument('-b', '--batch-size', type=int, nargs='?', default=1,
                    help='set batch size')
parser.add_argument('-l', '--lr', type=float, nargs='?', default=0.001,
                    help='set learning rate')
args = parser.parse_args()

dataset_dir = './data/object'
log_dir = os.path.join('./log', args.tag)
save_model_dir = os.path.join('./save_model', args.tag)
os.makedirs(log_dir, exist_ok=True)
os.makedirs(save_model_dir, exist_ok=True)
save_model_dir = os.path.join('./save_model', args.tag, 'checkpoint')


def main(_):
    # TODO: split file support
    global save_model_dir
    with KittiLoader(object_dir=os.path.join(dataset_dir, 'training'), queue_size=50, require_shuffle=True, 
            is_testset=False, batch_size=args.batch_size, use_multi_process_num=8) as train_loader, \
         KittiLoader(object_dir=os.path.join(dataset_dir, 'testing'), queue_size=50, require_shuffle=True, 
            is_testset=False, batch_size=args.batch_size, use_multi_process_num=8) as valid_loader :
        
        gpu_options = tf.GPUOptions(per_process_gpu_memory_fraction=cfg.GPU_MEMORY_FRACTION, 
            visible_device_list=cfg.GPU_AVAILABLE,
            allow_growth=True)
        config = tf.ConfigProto(
            gpu_options=gpu_options,
            device_count={
                "GPU" : cfg.GPU_USE_COUNT,  
            }
        )
        with tf.Session(config=config) as sess:
            model = RPN3D(
                cls=cfg.DETECT_OBJ,
                batch_size=args.batch_size,
                learning_rate=args.lr,
                max_gradient_norm=5.0,
                is_train=True,
                alpha=1.5,
                beta=1
            )
            # param init/restore
            if tf.train.get_checkpoint_state(save_model_dir):
                print("Reading model parameters from %s" % save_model_dir)
                model.saver.restore(sess, tf.train.latest_checkpoint(save_model_dir))
            else:
                print("Created model with fresh parameters.")
                tf.global_variables_initializer().run()

            # train and validate
            iter_per_epoch = int(len(train_loader)/args.batch_size)
            is_summary, is_summary_image, is_validate = False, False, False 
            
            summary_interval = 5
            summary_image_interval = 20
            save_model_interval = iter_per_epoch
            validate_interval = 100
            
            summary_writer = tf.summary.FileWriter(log_dir, sess.graph)
            while model.epoch.eval() < args.max_epoch:
                is_summary, is_summary_image, is_validate = False, False, False 
                iter = model.global_step.eval()
                if not iter % summary_interval:
                    is_summary = True
                if not iter % summary_image_interval:
                    is_summary_image = True 
                if not iter % save_model_interval:
                    model.saver.save(sess, save_model_dir, global_step=model.global_step)
                if not iter % validate_interval:
                    is_validate = True
                if not iter % iter_per_epoch:
                    sess.run(model.epoch_add_op)
                    print('train {} epoch, total: {}'.format(model.epoch.eval(), args.max_epoch))

                ret = model.train_step(sess, train_loader.load(), train=True, summary=is_summary)
                print('train: {}/{} @ epoch:{}/{} loss: {} reg_loss: {} cls_loss: {} {}'.format(iter, 
                    iter_per_epoch*args.max_epoch, model.epoch.eval(), args.max_epoch, ret[0], ret[1], ret[2], args.tag))

                if is_summary:
                    summary_writer.add_summary(ret[-1], iter)

                if is_summary_image:
                    ret = model.predict_step(sess, train_loader.load(), summary=True)
                    summary_writer.add_summary(ret[-1], iter)

                if is_validate:
                    ret = model.validate_step(sess, valid_loader.load(), summary=True)
                    summary_writer.add_summary(ret[-1], iter)
                
            print('train done. total epoch:{} iter:{}'.format(model.epoch.eval(), model.global_step.eval()))
            
            # finallly save model
            model.saver.save(sess, save_model_dir, global_step=model.global_step)

if __name__ == '__main__':
    tf.app.run(main)
