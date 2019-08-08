"""Code for training Image Synthesis + Segmentation"""

import surface_distance  
import json
import numpy as np
import os
import random
import argparse
import tensorflow as tf
import cyclegan_datasets
import data_loader2, losses, model2 

from scipy.misc import imsave
from skimage.measure import compare_psnr,compare_ssim,compare_mse
from datetime import datetime
from sklearn.metrics import mean_squared_error
from surface_distance import metrics           # calculate segmentation metrics HD95, Surface_distance,...



os.environ["CUDA_VISIBLE_DEVICES"]="5"         # Select GPU on Server

tf.set_random_seed(5)
np.random.seed(0)
slim = tf.contrib.slim


just_seg =0

infant=0
####################################			CycleGAN + Segmentation #############################

def dice(im1, im2,tid):   					  # Compute Dice score
    im1=im1==tid
    im2=im2==tid
    im1=np.asarray(im1).astype(np.bool)
    im2=np.asarray(im2).astype(np.bool)
    if im1.shape != im2.shape:
        raise ValueError("Shape mismatch: im1 and im2 must have the same shape.")
    intersection = np.logical_and(im1, im2)
    dsc=2. * intersection.sum() / (im1.sum() + im2.sum())
    return dsc

class CycleGAN_seg:
    """The CycleGAN module."""

    def __init__(self, pool_size, lambda_a,
                 lambda_b, output_root_dir, to_restore,
                 base_lr, max_step, network_version,
                 dataset_name, checkpoint_dir, do_flipping, skip, switch, threshold_fg,mymodel):
        current_time = datetime.now().strftime("%Y%m%d-%H%M%S")

        self._pool_size = pool_size
        self._size_before_crop = 128
        self._switch = switch
        self._threshold_fg = threshold_fg
        self._lambda_a = lambda_a
        self._lambda_b = lambda_b
        self._output_dir = os.path.join(output_root_dir, current_time +
                                        '_switch'+str(switch)+'_thres_'+str(threshold_fg))
        self._images_dir = os.path.join(self._output_dir, 'imgs')
        self._num_imgs_to_save = 4
        self._to_restore = to_restore
        self._base_lr = base_lr
        self._max_step = max_step
        self._network_version = network_version
        self._dataset_name = dataset_name
        self._checkpoint_dir = checkpoint_dir
        self._mymodel = mymodel
        self._do_flipping = do_flipping
        self._skip = skip

        self.fake_images_A = []
        self.fake_images_B = []

    def model_setup(self):
        """
        This function sets up the model to train.

        self.input_A/self.input_B -> Set of training images.
        self.fake_A/self.fake_B -> Generated images by corresponding generator
        of input_A and input_B
        self.lr -> Learning rate variable
        self.cyc_A/ self.cyc_B -> Images generated after feeding
        self.fake_A/self.fake_B to corresponding generator.
        This is use to calculate cyclic loss
	self.manual_seg -> manual labels in Ground truth
	self.auto_seg   -> predicted labels by the segmentation block
        """
        self.input_a = tf.placeholder(
            tf.float32, [
                1,
                model2.IMG_WIDTH,
                model2.IMG_HEIGHT,
                model2.IMG_CHANNELS
            ], name="input_A")
        self.input_b = tf.placeholder(
            tf.float32, [
                1,
                model2.IMG_WIDTH,
                model2.IMG_HEIGHT,
                model2.IMG_CHANNELS
            ], name="input_B")

        self.fake_pool_A = tf.placeholder(
            tf.float32, [
                None,
                model2.IMG_WIDTH,
                model2.IMG_HEIGHT,
                model2.IMG_CHANNELS
            ], name="fake_pool_A")
        self.fake_pool_B = tf.placeholder(
            tf.float32, [
                None,
                model2.IMG_WIDTH,
                model2.IMG_HEIGHT,
                model2.IMG_CHANNELS
            ], name="fake_pool_B")
        self.fake_pool_A_mask = tf.placeholder(
            tf.float32, [
                None,
                model2.IMG_WIDTH,
                model2.IMG_HEIGHT,
                model2.IMG_CHANNELS
            ], name="fake_pool_A_mask")
        self.fake_pool_B_mask = tf.placeholder(
            tf.float32, [
                None,
                model2.IMG_WIDTH,
                model2.IMG_HEIGHT,
                model2.IMG_CHANNELS
            ], name="fake_pool_B_mask")
        
        self.manual_seg = tf.placeholder(
            tf.float32, [
                None,
                model2.IMG_WIDTH,
                model2.IMG_HEIGHT,
                1
            ], name="seg_map")
            
        self.auto_seg = tf.placeholder(
            tf.float32, [
                None,
                model2.IMG_WIDTH,
                model2.IMG_HEIGHT,
                1
            ], name="seg_auto")            
            
             
            			
        self.global_step = slim.get_or_create_global_step()

        self.num_fake_inputs = 0

        self.learning_rate = tf.placeholder(tf.float32, shape=[], name="lr")
        self.transition_rate = tf.placeholder(tf.float32, shape=[], name="tr")
        self.donorm = tf.placeholder(tf.bool, shape=[], name="donorm")

        inputs = {
            'images_a': self.input_a,
            'images_b': self.input_b,
            'fake_pool_a': self.fake_pool_A,
            'fake_pool_b': self.fake_pool_B,
            'fake_pool_a_mask': self.fake_pool_A_mask,
            'fake_pool_b_mask': self.fake_pool_B_mask,
            'transition_rate': self.transition_rate,
            'donorm' : self.donorm,
            'seg_map':self.manual_seg                        
        }

        outputs = model2.get_outputs(
            inputs, skip=self._skip)

        self.prob_real_a_is_real = outputs['prob_real_a_is_real']
        self.prob_real_b_is_real = outputs['prob_real_b_is_real']
        self.fake_images_a = outputs['fake_images_a']
        self.fake_images_b = outputs['fake_images_b']
        self.prob_fake_a_is_real = outputs['prob_fake_a_is_real']
        self.prob_fake_b_is_real = outputs['prob_fake_b_is_real']

        self.cycle_images_a = outputs['cycle_images_a']
        self.cycle_images_b = outputs['cycle_images_b']

        self.prob_fake_pool_a_is_real = outputs['prob_fake_pool_a_is_real']
        self.prob_fake_pool_b_is_real = outputs['prob_fake_pool_b_is_real']
        self.masks = outputs['masks']
        self.masked_gen_ims = outputs['masked_gen_ims']
        self.masked_ims = outputs['masked_ims']
        self.masks_       = outputs['mask_tmp']
        self.prediction   = outputs['pred_seg']         ###########################################################################################

    def compute_losses(self):
        """
        In this function we are defining the variables for loss calculations
        and training model.

        d_loss_A/d_loss_B -> loss for discriminator A/B
        g_loss_A/g_loss_B -> loss for generator A/B
        *_trainer -> Various trainer for above loss functions
        *_summ -> Summary variables for above loss functions
        """
        
           
        
        self.seg_cost = tf.reduce_mean(tf.square(self.manual_seg-self.prediction))

        cycle_consistency_loss_a = \
            self._lambda_a * losses.cycle_consistency_loss(
                real_images=self.input_a, generated_images=self.cycle_images_a,
            )
        cycle_consistency_loss_b = \
            self._lambda_b * losses.cycle_consistency_loss(
                real_images=self.input_b, generated_images=self.cycle_images_b,
            )

        lsgan_loss_a = losses.lsgan_loss_generator(self.prob_fake_a_is_real)
        lsgan_loss_b = losses.lsgan_loss_generator(self.prob_fake_b_is_real)

        g_loss_A = \
            cycle_consistency_loss_a + cycle_consistency_loss_b + lsgan_loss_b + (100*self.seg_cost)
        g_loss_B = \
            cycle_consistency_loss_b + cycle_consistency_loss_a + lsgan_loss_a

        d_loss_A = losses.lsgan_loss_discriminator(
            prob_real_is_real=self.prob_real_a_is_real,
            prob_fake_is_real=self.prob_fake_pool_a_is_real,
        )
        d_loss_B = losses.lsgan_loss_discriminator(
            prob_real_is_real=self.prob_real_b_is_real,
            prob_fake_is_real=self.prob_fake_pool_b_is_real,
        )

        optimizer = tf.train.AdamOptimizer(self.learning_rate, beta1=0.5)
        self.model_vars = tf.trainable_variables()

        d_A_vars = [var for var in self.model_vars if 'd_A' in var.name]
        g_A_vars = [var for var in self.model_vars if 'g_A/' in var.name]
        d_B_vars = [var for var in self.model_vars if 'd_B' in var.name]
        g_B_vars = [var for var in self.model_vars if 'g_B/' in var.name]
        g_Ae_vars = [var for var in self.model_vars if 'g_A_ae' in var.name]
        g_Be_vars = [var for var in self.model_vars if 'g_B_ae' in var.name]
        
        seg_vars = [var for var in self.model_vars if 'seg_net' in var.name]
        
        


       # self.g_A_trainer = optimizer.minimize(g_loss_A, var_list=g_A_vars+g_Ae_vars)
        self.g_A_trainer = optimizer.minimize(g_loss_A, var_list=g_A_vars+g_Ae_vars+seg_vars)
        self.g_B_trainer = optimizer.minimize(g_loss_B, var_list=g_B_vars+g_Be_vars)
        self.g_A_trainer_bis = optimizer.minimize(g_loss_A, var_list=g_A_vars+seg_vars)
        self.g_B_trainer_bis = optimizer.minimize(g_loss_B, var_list=g_B_vars)
        self.d_A_trainer = optimizer.minimize(d_loss_A, var_list=d_A_vars)
        self.d_B_trainer = optimizer.minimize(d_loss_B, var_list=d_B_vars)
        
        self.seg_trainer = optimizer.minimize(self.seg_cost, var_list=seg_vars)

        self.params_ae_c1 = g_A_vars[0]
        self.params_ae_c1_B = g_B_vars[0]
        for var in self.model_vars:
            print(var.name)

        # Summary variables for tensorboard
        self.g_A_loss_summ = tf.summary.scalar("g_A_loss", g_loss_A)
        self.g_B_loss_summ = tf.summary.scalar("g_B_loss", g_loss_B)
        self.d_A_loss_summ = tf.summary.scalar("d_A_loss", d_loss_A)
        self.d_B_loss_summ = tf.summary.scalar("d_B_loss", d_loss_B)
        
        self.segmentation_loss_sum = tf.summary.scalar("segmentation_loss", self.seg_cost)
        
        
       # self.seg_loss_summ = tf.summary.scalar("d_B_loss", seg_trainer)
        
        

    def save_images(self, sess, epoch, curr_tr):
        """
        Saves input and output images.

        :param sess: The session.
        :param epoch: Currnt epoch.
        """
        if not os.path.exists(self._images_dir):
            os.makedirs(self._images_dir)

        if curr_tr >0:
            donorm = False
        else:
            donorm = True

        names = ['inputA_', 'inputB_','inputC_', 'fakeA_',
                 'fakeB_', 'cycA_', 'cycB_',
                 'mask_a', 'mask_b','seg']

        with open(os.path.join(
                self._output_dir, 'epoch_' + str(epoch) + '.html'
        ), 'w') as v_html:
            for i in range(0, self._num_imgs_to_save):
                print("Saving image {}/{}".format(i, self._num_imgs_to_save))
                inputs = sess.run(self.inputs)
                fake_A_temp, fake_B_temp, cyc_A_temp, cyc_B_temp, masks, pred_seg = sess.run([
                    self.fake_images_a,
                    self.fake_images_b,
                    self.cycle_images_a,
                    self.cycle_images_b,
                    self.masks,
                    self.prediction
                ], feed_dict={
                    self.input_a: inputs['images_i'],
                    self.input_b: inputs['images_j'],
                    
                    #self.manual_seg: inputs['images_c'],
                    
                    self.transition_rate: curr_tr,
                    self.donorm: donorm,
                })

                tensors = [inputs['images_i'], inputs['images_j'],inputs['images_c'],
                           fake_B_temp, fake_A_temp, cyc_A_temp, cyc_B_temp, masks[0], masks[1], pred_seg]

                for name, tensor in zip(names, tensors):
                    image_name = name + str(epoch) + "_" + str(i) + ".jpg"
                    if 'mask_' in name:
                        imsave(os.path.join(self._images_dir, image_name),
                               (np.squeeze(tensor[0]))
                               )
                    else:

                        imsave(os.path.join(self._images_dir, image_name),
                               ((np.squeeze(tensor[0]) + 1) * 127.5).astype(np.uint8)
                               )
                    v_html.write(
                        "<img src=\"" +
                        os.path.join('imgs', image_name) + "\">"
                    )
                v_html.write("<br>")

    def save_images_bis(self, sess, epoch):
        """
        Saves input and output images.

        :param sess: The session.
        :param epoch: Currnt epoch.
        """
        total_mse=[]
        total_mse_WM=[]
        total_mse_GM=[]
        total_mse_CSF=[]
        
        total_dsc_BG = []
        total_dsc_CSF = []
        total_dsc_GM = []
        total_dsc_WM =[]
        
        total_asd_WM  = []
        total_asd_GM  = []
        total_asd_CSF = []
        
        total_HD95_WM  = []
        total_HD95_GM  = []
        total_HD95_CSF = []
        
        total_dsc_lesion = []
        
         
         
         
        if not os.path.exists(self._images_dir):
            os.makedirs(self._images_dir)

        names = ['input_A_', 'mask_A_','input_C_', 'masked_inputA_', 'fakeB_',
                 'input_B_', 'mask_B_', 'masked_inputB_', 'fakeA_','seg']

        space = '&nbsp &nbsp &nbsp &nbsp &nbsp &nbsp &nbsp &nbsp &nbsp &nbsp &nbsp &nbsp ' \
                '&nbsp &nbsp &nbsp &nbsp &nbsp &nbsp &nbsp &nbsp &nbsp &nbsp &nbsp &nbsp ' \
                '&nbsp &nbsp &nbsp &nbsp &nbsp'
        with open(os.path.join(self._output_dir, 'results_' + str(epoch) + '.html'), 'w') as v_html:
            v_html.write("<b>INPUT" + space + "MASK" + space + "MASKED_IMAGE" + space + "GENERATED_IMAGE</b>")
            v_html.write("<br>")
            
            psnr_list=[]
            ssim_list=[]
            mse_list=[]
            for i in range(0, self._num_imgs_to_save):
                print("Saving image {}/{}".format(i, self._num_imgs_to_save))
                inputs = sess.run(self.inputs)
                fake_A_temp, fake_B_temp, masks, masked_ims, pred_seg = sess.run([
                    self.fake_images_a,
                    self.fake_images_b,
                    self.masks,
                    self.masked_ims,
                    self.prediction
                    
                ], feed_dict={
                    self.input_a: inputs['images_i'],
                    self.input_b: inputs['images_j'],
                    self.manual_seg: inputs['images_c'],
                    self.transition_rate: 0.1
                })
                
                #print(np.mean(tf.square(inputs['images_c']-pred_seg)))
                #mse = mean_squared_error(inputs['images_c'],pred_seg)
                #print(pred_seg.shape)
                print(np.min(pred_seg),np.max(pred_seg))
                print(np.min(inputs['images_c']),np.max(inputs['images_c']))
                
                IM1=np.copy(fake_B_temp[0,:,:,0])
                IM2 = np.copy(inputs['images_j'])
                IM2 = np.copy(IM2[0,:,:,0])
                
                #print(IM1.shape)
                psnr_list.append(compare_psnr(IM1,IM2))
                ssim_list.append(compare_ssim(IM1,IM2,multichannel=True))
                mse_list.append(compare_mse(IM1,IM2))
                
                
                
                
                
                                                                             
                tensors = [inputs['images_i'], masks[0], inputs['images_c'], masked_ims[0], fake_B_temp,
                           inputs['images_j'], masks[1], masked_ims[1], fake_A_temp, pred_seg]

                for name, tensor in zip(names, tensors):
                    image_name = name + str(i) + ".png"  #".jpg"

                    if 'mask_' in name:
                        imsave(os.path.join(self._images_dir, image_name),
                               (np.squeeze(tensor[0]))
                               )
                    else:

                        imsave(os.path.join(self._images_dir, image_name),
                               ((np.squeeze(tensor[0]) + 1) * 127.5).astype(np.uint8)
                               )

                    v_html.write(
                        "<img src=\"" +
                        os.path.join('imgs', image_name) + "\">"
                    )

                    if 'fakeB_' in name:
                        v_html.write("<br>")
                v_html.write("<br>")
            print(1-(sum(total_mse) / len(total_mse)) )
            total_np = np.array(total_mse)
            print('mean=%f,std=%f'%(100-np.mean(total_np)*100,np.std(total_np)))
            print(total_mse)
            print(len(total_mse))
            
            total_dsc_lesion = np.array(total_dsc_lesion)
            
            print('DSC_Lesion = %f ,STD = %f'%(np.mean(total_dsc_lesion)*100,np.std(total_dsc_lesion)))
             
            if (infant==1):
              total_np_WM = np.array(total_mse_WM)
              print('mean_WM=%f,std_WM=%f'%(100-np.mean(total_np_WM)*100,np.std(total_np_WM)))
              
              total_np_GM = np.array(total_mse_GM)
              print('mean_GM=%f,std_GM=%f'%(100-np.mean(total_np_GM)*100,np.std(total_np_GM)))
              
              total_np_CSF = np.array(total_mse_CSF)
              print('mean_CSF=%f,std_CSF=%f'%(100-np.mean(total_np_CSF)*100,np.std(total_np_CSF)))
              
              
              
              total_dsc_BG = np.array(total_dsc_BG)
              total_dsc_CSF = np.array(total_dsc_CSF)
              total_dsc_GM = np.array(total_dsc_GM)
              total_dsc_WM = np.array(total_dsc_WM)
              
              
              total_asd_WM = np.array(total_asd_WM)
              total_asd_GM = np.array(total_asd_GM)
              total_asd_CSF = np.array(total_asd_CSF)
              
              
              
              #print('mean_dsc_BG  = %f   ,dsc_BG = %f'%(np.mean(total_dsc_BG)*100,np.std(total_dsc_BG)))
             
              
              print('total_dsc_WM  = %f ,STD = %f'%(np.mean(total_dsc_WM)*100,np.std(total_dsc_WM)))
              print('total_dsc_GM  = %f ,STD = %f'%(np.mean(total_dsc_GM)*100,np.std(total_dsc_GM)))
              print('total_dsc_CSF = %f ,STD = %f'%(np.mean(total_dsc_CSF)*100,np.std(total_dsc_CSF)))
              
              print('total_asd_WM  = %f ,STD = %f'%(np.mean(total_asd_WM),np.std(total_asd_WM)))
              print('total_asd_GM  = %f ,STD = %f'%(np.mean(total_asd_GM),np.std(total_asd_GM)))
              print('total_asd_CSF = %f ,STD = %f'%(np.mean(total_asd_CSF),np.std(total_asd_CSF)))
              
              
              
              print('total_HD95_WM = %f ,STD = %f'%(np.mean(total_HD95_WM),np.std(total_HD95_WM)))
              print('total_HD95_GM = %f ,STD = %f'%(np.mean(total_HD95_GM),np.std(total_HD95_GM)))
              print('total_HD95_CSF= %f ,STD = %f'%(np.mean(total_HD95_CSF),np.std(total_HD95_CSF)))
              
              
              
              
            
            
            psnr_list=np.array(psnr_list)
            ssim_list=np.array(ssim_list)
            mse_list=np.array(mse_list)
            
            #print(psnr_list)
            print('psnr_list = ',np.mean(psnr_list))
            print('ssim_list = ',np.mean(ssim_list))
            print('mse_list = ',np.mean(mse_list))   
            
            
            
            
            
    def fake_image_pool(self, num_fakes, fake, mask, fake_pool):
        """
        This function saves the generated image to corresponding
        pool of images.

        It keeps on feeling the pool till it is full and then randomly
        selects an already stored image and replace it with new one.
        """
        tmp = {}
        tmp['im'] = fake
        tmp['mask'] = mask
        if num_fakes < self._pool_size:
            fake_pool.append(tmp)
            return tmp
        else:
            p = random.random()
            if p > 0.5:
                random_id = random.randint(0, self._pool_size - 1)
                temp = fake_pool[random_id]
                fake_pool[random_id] = tmp
                return temp
            else:
                return tmp

    def train(self):
        """Training Function."""
        # Load Dataset from the dataset folder
        self.inputs = data_loader2.load_data(
            self._dataset_name, self._size_before_crop,
            False, self._do_flipping)

        # Build the network
        self.model_setup()

        # Loss function calculations
        self.compute_losses()

        # Initializing the global variables
        init = (tf.global_variables_initializer(),
                tf.local_variables_initializer())

        saver = tf.train.Saver(max_to_keep=None)

        max_images = cyclegan_datasets.DATASET_TO_SIZES[self._dataset_name]
        half_training = int(self._max_step / 2)
        with tf.Session() as sess:
            sess.run(init)
            # Restore the model to run the model from last checkpoint
            if self._to_restore:
                chkpt_fname = tf.train.latest_checkpoint(self._checkpoint_dir)
                saver.restore(sess, chkpt_fname)

            writer = tf.summary.FileWriter(self._output_dir)

            if not os.path.exists(self._output_dir):
                os.makedirs(self._output_dir)

            coord = tf.train.Coordinator()
            threads = tf.train.start_queue_runners(coord=coord)

            # Training Loop
            for epoch in range(sess.run(self.global_step), self._max_step):
                print("In the epoch ", epoch)
               
                saver.save(sess, os.path.join(
                    self._output_dir, "AGGAN"), global_step=epoch)

                # Dealing with the learning rate as per the epoch number
                if epoch < half_training:
                    curr_lr = self._base_lr
                else:
                    curr_lr = self._base_lr - \
                        self._base_lr * (epoch - half_training) / half_training

                if epoch < self._switch:
                    curr_tr = 0.
                    donorm = True
                    to_train_A = self.g_A_trainer
                    to_train_B = self.g_B_trainer
                else:
                    curr_tr = self._threshold_fg
                    donorm = False
                    to_train_A = self.g_A_trainer_bis
                    to_train_B = self.g_B_trainer_bis


                self.save_images(sess, epoch, curr_tr)

                for i in range(0, max_images): #
                    print("Processing batch {}/{}".format(i, max_images))
                    
                    inputs = sess.run(self.inputs)
                    #print(inputs)
                    # Optimizing the Segmentation network
                    
                    if (just_seg==1):
                          _,summary_seg_str = sess.run(
                          [self.seg_trainer,self.segmentation_loss_sum],
                          feed_dict={
                              self.input_a:
                                  inputs['images_i'],
                              self.input_b:
                                  inputs['images_j'],
                              self.manual_seg:
                                  inputs['images_c'],   
                                  
                              self.learning_rate: curr_lr,
                              self.transition_rate: curr_tr,
                              self.donorm: donorm,
                          }
                      )
                          writer.add_summary(summary_seg_str, epoch * max_images + i)                      
                    else:
                      # Optimizing the G_A network
                      _, fake_B_temp, smask_a,summary_str,summary_str1 = sess.run(
                          [to_train_A,
                           self.fake_images_b,
                           self.masks[0],
                           self.g_A_loss_summ,self.segmentation_loss_sum],
                          feed_dict={
                              self.input_a:
                                  inputs['images_i'],
                              self.input_b:
                                  inputs['images_j'],
                              self.manual_seg:
                                  inputs['images_c'],   
                                  
                              self.learning_rate: curr_lr,
                              self.transition_rate: curr_tr,
                              self.donorm: donorm,
                          }
                      )
                      
                      #print(sess.run(self.seg_cost))
                      
                      #print(summary_str1)
                      writer.add_summary(summary_str, epoch * max_images + i)
                      #writer.add_summary(summary_str1, epoch * max_images + i)
                      
  
                      fake_B_temp1 = self.fake_image_pool(
                          self.num_fake_inputs, fake_B_temp, smask_a, self.fake_images_B)
  
                      # Optimizing the D_B network
                      _,summary_str = sess.run(
                          [self.d_B_trainer, self.d_B_loss_summ],
                          feed_dict={
                              self.input_a:
                                  inputs['images_i'],
                              self.input_b:
                                  inputs['images_j'],
                              self.learning_rate: curr_lr,
                              self.fake_pool_B: fake_B_temp1['im'],
                              self.fake_pool_B_mask: fake_B_temp1['mask'],
                              self.transition_rate: curr_tr,
                              self.donorm: donorm,
                          }
                      )
                      writer.add_summary(summary_str, epoch * max_images + i)
  
  
                      # Optimizing the G_B network
                      _, fake_A_temp, smask_b, summary_str = sess.run(
                          [to_train_B,
                           self.fake_images_a,
                           self.masks[1],
                           self.g_B_loss_summ],
                          feed_dict={
                              self.input_a:
                                  inputs['images_i'],
                              self.input_b:
                                  inputs['images_j'],
                              self.learning_rate: curr_lr,
                              self.transition_rate: curr_tr,
                              self.donorm: donorm,
                          }
                      )
                      writer.add_summary(summary_str, epoch * max_images + i)
  
                      fake_A_temp1 = self.fake_image_pool(
                          self.num_fake_inputs, fake_A_temp, smask_b ,self.fake_images_A)
  
                      # Optimizing the D_A network
                      _, mask_tmp__,summary_str = sess.run(
                          [self.d_A_trainer,self.masks_, self.d_A_loss_summ],
                          feed_dict={
                              self.input_a:
                                  inputs['images_i'],
                              self.input_b:
                                  inputs['images_j'],
                              self.learning_rate: curr_lr,
                              self.fake_pool_A: fake_A_temp1['im'],
                              self.fake_pool_A_mask: fake_A_temp1['mask'],
                              self.transition_rate: curr_tr,
                              self.donorm: donorm,
                          }
                      )
                      writer.add_summary(summary_str, epoch * max_images + i)
                      #self.test()
  
                      writer.flush()
                      self.num_fake_inputs += 1

                sess.run(tf.assign(self.global_step, epoch + 1))
                #writer.add_summary(summary_str1, epoch)
            
            coord.request_stop()
            coord.join(threads)
            writer.add_graph(sess.graph)

    def test(self):
        """Test Function."""
        print("Testing the results")

        self.inputs = data_loader2.load_data(
            self._dataset_name, self._size_before_crop,
            False, self._do_flipping)

        self.model_setup()
        saver = tf.train.Saver()
        init = tf.global_variables_initializer()

        total_parameters = 0
        


        with tf.Session() as sess:
            sess.run(init)
            
            
            
            if (self._mymodel != ''):
                chkpt_fname = self._mymodel
                saver.restore(sess, chkpt_fname)
            else:  
                chkpt_fname = tf.train.latest_checkpoint(self._checkpoint_dir)
                saver.restore(sess, chkpt_fname)
            
            for variable in tf.trainable_variables():
          # shape is an array of tf.Dimension
              shape = variable.get_shape()
              variable_parameters = 1
              for dim in shape:
          #print(dim)
                variable_parameters *= dim.value
          #print(variable_parameters)
            total_parameters += variable_parameters
            print("tatal parameters = ",total_parameters)

            coord = tf.train.Coordinator()
            threads = tf.train.start_queue_runners(coord=coord)

            self._num_imgs_to_save = cyclegan_datasets.DATASET_TO_SIZES[
                self._dataset_name]
            self.save_images_bis(sess, sess.run(self.global_step))

            coord.request_stop()
            coord.join(threads)

			
#################			
			
def parse_args():                             # read argumants
    desc = "Tensorflow implementation of cycleGan using attention"
    parser = argparse.ArgumentParser(description=desc)

    parser.add_argument('--to_train', type=int, default=True, help='Whether it is train or false.')
    parser.add_argument('--log_dir',
              type=str,
              default=None,
              help='Where the data is logged to.')

    parser.add_argument('--config_filename', type=str, default='train', help='The name of the configuration file.')

    parser.add_argument('--checkpoint_dir', type=str, default='', help='The name of the train/test split.')
    parser.add_argument('--skip', type=bool, default=False,
                        help='Whether to add skip connection between input and output.')
    parser.add_argument('--switch', type=int, default=30,
                        help='In what epoch the FG starts to be fed to the discriminator')
    parser.add_argument('--threshold', type=float, default=0.1,
                        help='The threshold value to select the FG')
                        
    parser.add_argument('--mymodel', type=str, default='',
                        help='model num')                        


    return parser.parse_args()

def main():                                   
    """
    :param to_train: Specify whether it is training or testing. 1: training; 2:
     resuming from latest checkpoint; 0: testing.
    :param log_dir: The root dir to save checkpoints and imgs. The actual dir
    is the root dir appended by the folder with the name timestamp.
    :param config_filename: The configuration file.
    :param checkpoint_dir: The directory that saves the latest checkpoint. It
    only takes effect when to_train == 2.
    :param skip: A boolean indicating whether to add skip connection between
    input and output.
    """
    args = parse_args()
    if args is None:
        exit()
    mymodel		= args.mymodel
    to_train 	= args.to_train
    log_dir 	= args.log_dir
    config_filename = args.config_filename
    checkpoint_dir	= args.checkpoint_dir
    skip 			= args.skip
    switch 			= args.switch
    threshold_fg 	= args.threshold

    if not os.path.isdir(log_dir):
        os.makedirs(log_dir)

    with open(config_filename) as config_file:
        config = json.load(config_file)



    lambda_a = float(config['_LAMBDA_A']) if '_LAMBDA_A' in config else 10.0
    lambda_b = float(config['_LAMBDA_B']) if '_LAMBDA_B' in config else 10.0
    pool_size = int(config['pool_size']) if 'pool_size' in config else 50

    to_restore = (to_train == 2)
    base_lr = float(config['base_lr']) if 'base_lr' in config else 0.0002
    max_step = int(config['max_step']) if 'max_step' in config else 200
    network_version = str(config['network_version'])
    dataset_name = str(config['dataset_name'])
    do_flipping = bool(config['do_flipping'])

    cyclegan_model_seg = CycleGAN_seg(pool_size, lambda_a, lambda_b, log_dir,
                              to_restore, base_lr, max_step, network_version,
                              dataset_name, checkpoint_dir, do_flipping, skip,
                              switch, threshold_fg,mymodel)
							  
					  

    if to_train > 0:
        cyclegan_model_seg.train()
    else:
        cyclegan_model_seg.test()

if __name__ == '__main__':
    main()
 