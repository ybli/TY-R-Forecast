clear
python run_trajGRU_same_warp.py --able-cuda --lr-scheduler --clip --input-with-grid --input-with-QPE --normalize-target \
--gpu 0 --lr 0.5 --weight-decay 0 --batch-size 3 --optimizer Adam --value-dtype float16 \
--I-x-l 118.9 --I-x-h 122.2675 --I-y-l 21.75 --I-y-h 25.1125 \
--F-x-l 118.9 --F-x-h 122.2675 --F-y-l 21.75 --F-y-h 25.1125
