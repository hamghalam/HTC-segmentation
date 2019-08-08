# HTC-segmentation
Generate high-tissue contrast images for the segmentation task

# Prerequisites

<p> Linux or OSX </p>
<p> NVIDIA GPU + CUDA CuDNN  </p> 
<p> TensorFlow  </p>

# Prepare dataset

<div class="highlight highlight-source-shell"><pre>
python -m create_cyclegan_dataset2 --image_path_a='./input/low2high64_ET/trainA/' --image_path_b='./input/low2high64_ET/trainB/'  --image_path_c='./input/low2high64_ET/trainC/' --dataset_name="l2h64_ET_train" --do_shuffle=0
</pre></div>

# Training

<div class="highlight highlight-source-shell"><pre>
python main2.py  --to_train=1 --log_dir=./output/AGGAN/exp_01 --config_filename=./configs/l2h64_ET.json
</pre></div>

# Testing

<div class="highlight highlight-source-shell"><pre>
python main2.py --to_train=0 --log_dir=./output/AGGAN/exp_01 --config_filename=./configs/l2h64_ET_test.json --checkpoint_dir=./output/AGGAN/exp_01/20190604-172048_switch30_thres_0.1
</pre></div>
