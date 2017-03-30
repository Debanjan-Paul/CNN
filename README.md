# CNN
Predicting review score from review content
Requirements

Code is written in Python (2.7) and requires Theano (0.7).

Using the pre-trained word2vec vectors will also require downloading the binary file from https://code.google.com/p/word2vec/

Data Preprocessing

To process the raw data, run

python new_process_data.py path

where path points to the word2vec binary file (i.e. GoogleNews-vectors-negative300.bin file).
This will create a pickle object called mr.p in the same folder, which contains the dataset in the right format.

Running the models (CPU)

Example commands:


THEANO_FLAGS=mode=FAST_RUN,device=cpu,floatX=float32 python new_conv_net_sentence.py -static -word2vec

Input Files Description:
one.txt = review texts of quality score 1.0
two.txt = review texts of quality score 2.0
three.txt = review texts of quality score 3.0
four.txt = review texts of quality score 4.0
five.txt = review texts of quality score 5.0

test.txt = review texts only whose quality you want to evaluate

Sample input files are uploaded.

Changes in code:
In line no. 341 and 346 : set the correct max sentence length which is given as ouput from new_process_data.py
In line no. 264, 280 and 297 : set the correct max sentence length= (max_sentence_length-5)  which is given as ouput from new_process_data.py
