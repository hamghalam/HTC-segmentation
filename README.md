# HTC-segmentation
Generate high-tissue contrast for segmentation

# Prerequisites

Linux or OSX
NVIDIA GPU + CUDA CuDNN 
TensorFlow

# Prepare dataset

python -m create_cyclegan_dataset2 --image_path_a='./input/low2high64_ET/trainA/' --image_path_b='./input/low2high64_ET/trainB/'  --image_path_c='./input/low2high64_ET/trainC/' --dataset_name="l2h64_ET_train" --do_shuffle=0

# Training

python main2.py  --to_train=1 --log_dir=./output/AGGAN/exp_01 --config_filename=./configs/l2h64_ET.json

# Testing

python main2.py --to_train=0 --log_dir=./output/AGGAN/exp_01 --config_filename=./configs/l2h64_ET_test.json --checkpoint_dir=./output/AGGAN/exp_01/20190604-172048_switch30_thres_0.1
