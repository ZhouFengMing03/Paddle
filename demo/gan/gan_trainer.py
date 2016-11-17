# Copyright (c) 2016 Baidu, Inc. All Rights Reserved
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import itertools
import random
import numpy

from paddle.trainer.config_parser import parse_config
from paddle.trainer.config_parser import logger
import py_paddle.swig_paddle as api
from py_paddle import DataProviderConverter

import matplotlib.pyplot as plt


def plot2DScatter(data, outputfile):
    # Generate some test data
    x = data[:, 0]
    y = data[:, 1]
    print "The mean vector is %s" % numpy.mean(data, 0)
    print "The std vector is %s" % numpy.std(data, 0)

    heatmap, xedges, yedges = numpy.histogram2d(x, y, bins=50)
    extent = [xedges[0], xedges[-1], yedges[0], yedges[-1]]

    plt.clf()
    plt.scatter(x, y)
    # plt.show()
    plt.savefig(outputfile, bbox_inches='tight')

def CHECK_EQ(a, b):
    assert a == b, "a=%s, b=%s" % (a, b)


def copy_shared_parameters(src, dst):
    src_params = [src.getParameter(i)
               for i in xrange(src.getParameterSize())]
    src_params = dict([(p.getName(), p) for p in src_params])


    for i in xrange(dst.getParameterSize()):
        dst_param = dst.getParameter(i)
        src_param = src_params.get(dst_param.getName(), None)
        if src_param is None:
            continue
        src_value = src_param.getBuf(api.PARAMETER_VALUE)
        dst_value = dst_param.getBuf(api.PARAMETER_VALUE)
        CHECK_EQ(len(src_value), len(dst_value))
        dst_value.copyFrom(src_value)
        dst_param.setValueUpdated()
        
def print_parameters(src):
    src_params = [src.getParameter(i)
               for i in xrange(src.getParameterSize())]

    print "***************"
    for p in src_params:
        print "Name is %s" % p.getName()
        print "value is %s \n" % p.getBuf(api.PARAMETER_VALUE).copyToNumpyArray()
        
def get_real_samples(batch_size, sample_dim):
    return numpy.random.rand(batch_size, sample_dim).astype('float32')
    # return numpy.random.normal(loc=100.0, scale=100.0, size=(batch_size, sample_dim)).astype('float32')

def get_fake_samples(generator_machine, batch_size, noise_dim, sample_dim):
    gen_inputs = prepare_generator_data_batch(batch_size, noise_dim)
    gen_inputs.resize(1)
    gen_outputs = api.Arguments.createArguments(0)
    generator_machine.forward(gen_inputs, gen_outputs, api.PASS_TEST)
    fake_samples = gen_outputs.getSlotValue(0).copyToNumpyMat()
    return fake_samples

def get_training_loss(training_machine, inputs):
    outputs = api.Arguments.createArguments(0)
    training_machine.forward(inputs, outputs, api.PASS_TEST)
    loss = outputs.getSlotValue(0).copyToNumpyMat()
    return numpy.mean(loss)

def prepare_discriminator_data_batch(
        generator_machine, batch_size, noise_dim, sample_dim):
    fake_samples = get_fake_samples(generator_machine, batch_size / 2, noise_dim, sample_dim)
    real_samples = get_real_samples(batch_size / 2, sample_dim)
    all_samples = numpy.concatenate((fake_samples, real_samples), 0)
    all_labels = numpy.concatenate(
        (numpy.zeros(batch_size / 2, dtype='int32'),
         numpy.ones(batch_size / 2, dtype='int32')), 0)
    inputs = api.Arguments.createArguments(2)
    inputs.setSlotValue(0, api.Matrix.createGpuDenseFromNumpy(all_samples))
    inputs.setSlotIds(1, api.IVector.createGpuVectorFromNumy(all_labels))
    return inputs

def prepare_discriminator_data_batch_pos(batch_size, noise_dim, sample_dim):
    real_samples = get_real_samples(batch_size, sample_dim)
    labels = numpy.ones(batch_size, dtype='int32')
    inputs = api.Arguments.createArguments(2)
    inputs.setSlotValue(0, api.Matrix.createGpuDenseFromNumpy(real_samples))
    inputs.setSlotIds(1, api.IVector.createGpuVectorFromNumpy(labels))
    return inputs

def prepare_discriminator_data_batch_neg(generator_machine, batch_size, noise_dim, sample_dim):
    fake_samples = get_fake_samples(generator_machine, batch_size, noise_dim, sample_dim)
    labels = numpy.zeros(batch_size, dtype='int32')
    inputs = api.Arguments.createArguments(2)
    inputs.setSlotValue(0, api.Matrix.createGpuDenseFromNumpy(fake_samples))
    inputs.setSlotIds(1, api.IVector.createGpuVectorFromNumpy(labels))
    return inputs

def prepare_generator_data_batch(batch_size, dim):
    noise = numpy.random.normal(size=(batch_size, dim)).astype('float32')
    label = numpy.ones(batch_size, dtype='int32')
    inputs = api.Arguments.createArguments(2)
    inputs.setSlotValue(0, api.Matrix.createGpuDenseFromNumpy(noise))
    inputs.setSlotIds(1, api.IVector.createGpuVectorFromNumpy(label))
    return inputs


def find(iterable, cond):
    for item in iterable:
        if cond(item):
            return item
    return None


def get_layer_size(model_conf, layer_name):
    layer_conf = find(model_conf.layers, lambda x: x.name == layer_name)
    assert layer_conf is not None, "Cannot find '%s' layer" % layer_name
    return layer_conf.size


def main():
    api.initPaddle('--use_gpu=1', '--dot_period=10', '--log_period=100', 
                   '--gpu_id=2')
    gen_conf = parse_config("gan_conf.py", "mode=generator_training")
    dis_conf = parse_config("gan_conf.py", "mode=discriminator_training")
    generator_conf = parse_config("gan_conf.py", "mode=generator")
    batch_size = dis_conf.opt_config.batch_size
    noise_dim = get_layer_size(gen_conf.model_config, "noise")
    sample_dim = get_layer_size(dis_conf.model_config, "sample")

    # this create a gradient machine for discriminator
    dis_training_machine = api.GradientMachine.createFromConfigProto(
        dis_conf.model_config)

    gen_training_machine = api.GradientMachine.createFromConfigProto(
        gen_conf.model_config)

    # generator_machine is used to generate data only, which is used for
    # training discrinator
    logger.info(str(generator_conf.model_config))
    generator_machine = api.GradientMachine.createFromConfigProto(
        generator_conf.model_config)

    dis_trainer = api.Trainer.create(
        dis_conf, dis_training_machine)

    gen_trainer = api.Trainer.create(
        gen_conf, gen_training_machine)

    dis_trainer.startTrain()
    gen_trainer.startTrain()
    copy_shared_parameters(gen_training_machine, dis_training_machine)
    copy_shared_parameters(gen_training_machine, generator_machine)
    curr_train = "dis"
    curr_strike = 0
    MAX_strike = 5
    
    for train_pass in xrange(100):
        dis_trainer.startTrainPass()
        gen_trainer.startTrainPass()
        for i in xrange(1000):
#             data_batch_dis = prepare_discriminator_data_batch(
#                     generator_machine, batch_size, noise_dim, sample_dim)
#             dis_loss = get_training_loss(dis_training_machine, data_batch_dis)
            data_batch_dis_pos = prepare_discriminator_data_batch_pos(
                batch_size, noise_dim, sample_dim)
            dis_loss_pos = get_training_loss(dis_training_machine, data_batch_dis_pos)
            
            data_batch_dis_neg = prepare_discriminator_data_batch_neg(
                generator_machine, batch_size, noise_dim, sample_dim)
            dis_loss_neg = get_training_loss(dis_training_machine, data_batch_dis_neg)            
            
            dis_loss = (dis_loss_pos + dis_loss_neg) / 2.0
            
            data_batch_gen = prepare_generator_data_batch(
                    batch_size, noise_dim)
            gen_loss = get_training_loss(gen_training_machine, data_batch_gen)
            
            if i % 1000 == 0:
                print "d_loss is %s    g_loss is %s" % (dis_loss, gen_loss)
                            
            if (not (curr_train == "dis" and curr_strike == MAX_strike)) and ((curr_train == "gen" and curr_strike == MAX_strike) or dis_loss > gen_loss):
                if curr_train == "dis":
                    curr_strike += 1
                else:
                    curr_train = "dis"
                    curr_strike = 1                
                dis_trainer.trainOneDataBatch(batch_size, data_batch_dis_neg)
                dis_trainer.trainOneDataBatch(batch_size, data_batch_dis_pos)
#                 dis_loss = numpy.mean(dis_trainer.getForwardOutput()[0]["value"])
#                 print "getForwardOutput loss is %s" % dis_loss                
                copy_shared_parameters(dis_training_machine, gen_training_machine)

            else:
                if curr_train == "gen":
                    curr_strike += 1
                else:
                    curr_train = "gen"
                    curr_strike = 1
                gen_trainer.trainOneDataBatch(batch_size, data_batch_gen)    
                copy_shared_parameters(gen_training_machine, dis_training_machine)
                copy_shared_parameters(gen_training_machine, generator_machine)

        dis_trainer.finishTrainPass()
        gen_trainer.finishTrainPass()

        fake_samples = get_fake_samples(generator_machine, batch_size, noise_dim, sample_dim)
        plot2DScatter(fake_samples, "./train_pass%s.png" % train_pass)
    dis_trainer.finishTrain()
    gen_trainer.finishTrain()

if __name__ == '__main__':
    main()
