import cPickle
import numpy as np
from collections import defaultdict, OrderedDict
import theano
import theano.tensor as T
import re
import warnings
import sys
import time
warnings.filterwarnings("ignore")   


def ReLU(x):
    y = T.maximum(0.0, x)
    return(y)
def Sigmoid(x):
    y = T.nnet.sigmoid(x)
    return(y)
def Tanh(x):
    y = T.tanh(x)
    return(y)
def Iden(x):
    y = x
    return(y)
       
def train_conv_net(datasets, U, img_w=300, filter_hs=[3,4,5], hidden_units=[100,5],  dropout_rate=[0.5], shuffle_batch=True,
                   n_epochs=25, batch_size=50, lr_decay = 0.95, conv_non_linear="relu", activations=[Iden], sqr_norm_lim=9,
                   non_static=True):
    """
    Train a simple conv net
    img_h = sentence length (padded where necessary)
    img_w = word vector length (300 for word2vec)
    filter_hs = filter window sizes    
    hidden_units = [x,y] x is the number of feature maps (per filter window), and y is the penultimate layer
    sqr_norm_lim = s^2 in the paper
    lr_decay = adadelta decay parameter
    """    
    rng = np.random.RandomState(3435)
    img_h = len(datasets[0][0])-1  
    filter_w = img_w    
    feature_maps = hidden_units[0]
    filter_shapes = []
    pool_sizes = []
    for filter_h in filter_hs:
        filter_shapes.append((feature_maps, 1, filter_h, filter_w))
        pool_sizes.append((img_h-filter_h+1, img_w-filter_w+1))
    parameters = [("image shape",img_h,img_w),("filter shape",filter_shapes), ("hidden_units",hidden_units),
                  ("dropout", dropout_rate), ("batch_size",batch_size),("non_static", non_static),
                    ("learn_decay",lr_decay), ("conv_non_linear", conv_non_linear), ("non_static", non_static)
                    ,("sqr_norm_lim",sqr_norm_lim),("shuffle_batch",shuffle_batch)]
    print parameters    
    
    #define model architecture
    index = T.lscalar()
    x = T.matrix('x')   
    y = T.ivector('y')
    Words = theano.shared(value = U, name = "Words")
    zero_vec_tensor = T.vector()
    zero_vec = np.zeros(img_w)
    set_zero = theano.function([zero_vec_tensor], updates=[(Words, T.set_subtensor(Words[0,:], 
                                zero_vec_tensor))], allow_input_downcast=True)
    layer0_input = Words[T.cast(x.flatten(),dtype="int32")].reshape((x.shape[0],1,x.shape[1],
                                                                     Words.shape[1]))                                  
    conv_layers = []
    layer1_inputs = []
    for i in xrange(len(filter_hs)):
        filter_shape = filter_shapes[i]
        pool_size = pool_sizes[i]
        conv_layer = LeNetConvPoolLayer(rng, input=layer0_input,image_shape=(batch_size, 1, img_h, img_w),
                                filter_shape=filter_shape, poolsize=pool_size, non_linear=conv_non_linear)
        layer1_input = conv_layer.output.flatten(2)
        conv_layers.append(conv_layer)
        layer1_inputs.append(layer1_input)
    layer1_input = T.concatenate(layer1_inputs,1)
    hidden_units[0] = feature_maps*len(filter_hs)    
    classifier = MLPDropout(rng, input=layer1_input, layer_sizes=hidden_units, activations=activations, 
                            dropout_rates=dropout_rate)
    
    #define parameters of the model and update functions using adadelta
    params = classifier.params     
    for conv_layer in conv_layers:
        params += conv_layer.params
    if non_static:
        #if word vectors are allowed to change, add them as model parameters
        params += [Words]
    cost = classifier.negative_log_likelihood(y) 
    dropout_cost = classifier.dropout_negative_log_likelihood(y)           
    grad_updates = sgd_updates_adadelta(params, dropout_cost, lr_decay, 1e-6, sqr_norm_lim)
    
    #shuffle dataset and assign to mini batches. if dataset size is not a multiple of mini batches, replicate 
    #extra data (at random)
    np.random.seed(3435)
    if datasets[0].shape[0] % batch_size > 0:
        extra_data_num = batch_size - datasets[0].shape[0] % batch_size
        train_set = np.random.permutation(datasets[0])   
        extra_data = train_set[:extra_data_num]
        new_data=np.append(datasets[0],extra_data,axis=0)
    else:
        new_data = datasets[0]
    new_data = np.random.permutation(new_data)
    
    n_batches = new_data.shape[0]/batch_size
    n_train_batches = int(np.round(n_batches*0.9))
   
    train_set = new_data[:n_batches*batch_size,:]
    val_set = datasets[1]
    test_set_x = datasets[2]

    
    train_set_x, train_set_y = shared_dataset((train_set[:,:img_h],train_set[:,-1]))
    val_set_x, val_set_y = shared_dataset((val_set[:,:img_h],val_set[:,-1]))
    n_val_batches = n_batches - n_train_batches
    
    get_acc_val_model = theano.function([index], classifier.errors(y),
        givens={
            x: val_set_x[index * batch_size: (index + 1) * batch_size],
                y: val_set_y[index * batch_size: (index + 1) * batch_size]},
                                allow_input_downcast=True)
            
    #compile theano functions to get train/val/test errors
    get_acc_train_model = theano.function([index], classifier.errors(y),
        givens={
            x: train_set_x[index * batch_size: (index + 1) * batch_size],
                y: train_set_y[index * batch_size: (index + 1) * batch_size]},
                                allow_input_downcast=True)               
    train_model = theano.function([index], cost, updates=grad_updates,
        givens={
            x: train_set_x[index*batch_size:(index+1)*batch_size],
                y: train_set_y[index*batch_size:(index+1)*batch_size]},
                                allow_input_downcast = True)     
    test_pred_layers_a = []
    test_pred_layers_b = []
    
    
    # If test set is large loading the whole test set can give GPU memory allocation error
    # So we make prediction only by taking a maximum of 2000 test examples at a time
    test_size = test_set_x.shape[0]
    test_batch_size = 2000
    test_iter = int(test_size/test_batch_size)
    extra_test_size = test_size - test_iter * test_batch_size
    
    test_layer0_input_a = Words[T.cast(x.flatten(),dtype="int32")].reshape((test_batch_size,1,img_h,Words.shape[1]))
    for conv_layer in conv_layers:
        test_layer0_output_a = conv_layer.predict(test_layer0_input_a, test_batch_size)
        test_pred_layers_a.append(test_layer0_output_a.flatten(2))
    test_layer1_input_a = T.concatenate(test_pred_layers_a, 1)
    test_y_pred_a = classifier.predict(test_layer1_input_a)
    
    
    test_layer0_input_b = Words[T.cast(x.flatten(),dtype="int32")].reshape((extra_test_size,1,img_h,Words.shape[1]))
    for conv_layer in conv_layers:
        test_layer0_output_b = conv_layer.predict(test_layer0_input_b, extra_test_size)
        test_pred_layers_b.append(test_layer0_output_b.flatten(2))
    test_layer1_input_b = T.concatenate(test_pred_layers_b, 1)
    test_y_pred_b = classifier.predict(test_layer1_input_b)
    
    
    test_model_all_a = theano.function([x], test_y_pred_a, allow_input_downcast = True)   
    test_model_all_b = theano.function([x], test_y_pred_b, allow_input_downcast = True) 
    
    #start training over mini-batches
    print '... training'
    epoch = 0
    best_val_perf = 0
    val_perf = 0
    test_perf = 0       
    cost_epoch = 0  
    while (epoch < n_epochs):
        start_time = time.time()
        epoch = epoch + 1
        if shuffle_batch:
            for minibatch_index in np.random.permutation(range(n_train_batches)):
                cost_epoch = train_model(minibatch_index)
                set_zero(zero_vec)
        else:
            for minibatch_index in xrange(n_train_batches):
                cost_epoch = train_model(minibatch_index)  
                set_zero(zero_vec)
        train_losses = [get_acc_train_model(i) for i in xrange(n_train_batches)]
        train_perf = 1 - np.mean(train_losses)
        val_losses = [get_acc_val_model(i) for i in xrange(n_val_batches)]
        val_perf = 1- np.mean(val_losses)                        
        print('epoch: %i, training time: %.2f secs, train perf: %.2f %%, val perf: %.2f %%' % (epoch, 
                                         time.time()-start_time, train_perf * 100., val_perf*100.))

    prediction1 = np.zeros((test_iter, test_batch_size))
    for j in xrange(test_iter):
        prediction1[j] = test_model_all_a(test_set_x[test_batch_size*j:test_batch_size*(j+1) , :])
        
    prediction2 = test_model_all_b(test_set_x[-extra_test_size: , :])    
    prediction = list(prediction1.reshape(test_batch_size * test_iter)) + list(prediction2)
    savefile = file('obj.save', 'wb')
    cPickle.dump(params,savefile,protocol=cPickle.HIGHEST_PROTOCOL)
    return prediction
        
def shared_dataset(data_xy, borrow=True):
        """ Function that loads the dataset into shared variables

        The reason we store our dataset in shared variables is to allow
        Theano to copy it into the GPU memory (when code is run on GPU).
        Since copying data into the GPU is slow, copying a minibatch everytime
        is needed (the default behaviour if the data is not in a shared
        variable) would lead to a large decrease in performance.
        """
        data_x, data_y = data_xy
        shared_x = theano.shared(np.asarray(data_x,
                                               dtype=theano.config.floatX),
                                 borrow=borrow)
        shared_y = theano.shared(np.asarray(data_y,
                                               dtype=theano.config.floatX),
                                 borrow=borrow)
        return shared_x, T.cast(shared_y, 'int32')
        
def sgd_updates_adadelta(params,cost,rho=0.95,epsilon=1e-6,norm_lim=9,word_vec_name='Words'):
    """
    adadelta update rule, mostly from
    https://groups.google.com/forum/#!topic/pylearn-dev/3QbKtCumAW4 (for Adadelta)
    """
    updates = OrderedDict({})
    exp_sqr_grads = OrderedDict({})
    exp_sqr_ups = OrderedDict({})
    gparams = []
    for param in params:
        empty = np.zeros_like(param.get_value())
        exp_sqr_grads[param] = theano.shared(value=as_floatX(empty),name="exp_grad_%s" % param.name)
        gp = T.grad(cost, param)
        exp_sqr_ups[param] = theano.shared(value=as_floatX(empty), name="exp_grad_%s" % param.name)
        gparams.append(gp)
    for param, gp in zip(params, gparams):
        exp_sg = exp_sqr_grads[param]
        exp_su = exp_sqr_ups[param]
        up_exp_sg = rho * exp_sg + (1 - rho) * T.sqr(gp)
        updates[exp_sg] = up_exp_sg
        step =  -(T.sqrt(exp_su + epsilon) / T.sqrt(up_exp_sg + epsilon)) * gp
        updates[exp_su] = rho * exp_su + (1 - rho) * T.sqr(step)
        stepped_param = param + step
        if (param.get_value(borrow=True).ndim == 2) and (param.name!='Words'):
            col_norms = T.sqrt(T.sum(T.sqr(stepped_param), axis=0))
            desired_norms = T.clip(col_norms, 0, T.sqrt(norm_lim))
            scale = desired_norms / (1e-7 + col_norms)
            updates[param] = stepped_param * scale
        else:
            updates[param] = stepped_param      
    return updates 

def as_floatX(variable):
    if isinstance(variable, float):
        return np.cast[theano.config.floatX](variable)

    if isinstance(variable, np.ndarray):
        return np.cast[theano.config.floatX](variable)
    return theano.tensor.cast(variable, theano.config.floatX)
    
def safe_update(dict_to, dict_from):
    """
    re-make update dictionary for safe updating
    """
    for key, val in dict(dict_from).iteritems():
        if key in dict_to:
            raise KeyError(key)
        dict_to[key] = val
    return dict_to
    
def get_idx_from_sent(sent, word_idx_map, max_l=155, k=300, filter_h=5):
    """
    Transforms sentence into a list of indices. Pad with zeroes.
    """
    x = []
    pad = filter_h - 1
    for i in xrange(pad):
        x.append(0)
    words = sent.split()
    for word in words:
        if word in word_idx_map:
            x.append(word_idx_map[word])
    while len(x) < max_l+2*pad:
        x.append(0)
    return x

def make_idx_train_valid_data(train_revs, word_idx_map, cv, max_l =155, k = 300, filter_h = 5):
    """
    Transforms sentences into a 2-d matrix.
    """
    train, valid = [], []
    for rev in train_revs:
        sent = get_idx_from_sent(rev["text"], word_idx_map, max_l, k, filter_h)   
        sent.append(rev["y"])
        if rev["split"]==cv:            
            valid.append(sent)        
        else:  
            train.append(sent)
 
    train = np.array(train,dtype="int")
    valid = np.array(valid,dtype='int' )
    return [train, valid]    

def make_idx_test_data(test_revs, word_idx_map, max_l = 155, k = 300, filter_h = 5):
    """
    Transforms sentences into a 2-d matrix.
    """
    test = []
    for rev in test_revs:
        sent = get_idx_from_sent(rev["text"], word_idx_map, max_l, k, filter_h)   
        test.append(sent)  
        
    test = np.array(test,dtype="int")
    return test
	
if __name__=="__main__":
	print 'Loading data...',
	x = cPickle.load(open("mr.p","rb"))
	train_revs, test_revs, W1, W3, word_idx_map, vocab = x[0], x[1], x[2], x[3], x[4], x[5]
	print "data loaded!"
		
	mode= 'nonstatic'
	word_vectors = 'Word2Vec'
		
		
	if mode=="nonstatic":
		print "Model architecture: CNN-non-static"
		non_static=True
	elif mode=="static":
		print "Model architecture: CNN-static"
		non_static=False
		
	execfile("conv_net_classes.py")    

	if word_vectors=="Word2Vec":
		print "Using: Word2Vec "
		U = W1
	elif word_vectors=="GloVe":
		print "Using: GloVe "
		U = W2
	elif word_vectors=='Random':
		print "Using: Random vectors"
		U = W3
	results = []
	
	
	#y_test = np.array(list(np.zeros(50)) + list(np.ones(50)))   
	test = make_idx_test_data(test_revs, word_idx_map, max_l = 160, k = 300, filter_h = 5)

	r = range(0,3)    

	for i in r:
		datasets = make_idx_train_valid_data(train_revs, word_idx_map, i, max_l = 160, k = 300, filter_h = 5)
		datasets.append(test)
		y_prediction = train_conv_net(datasets,U, lr_decay=0.95, filter_hs=[3,4,5], conv_non_linear="relu", hidden_units=[16,5], 
								shuffle_batch=True, n_epochs=25, sqr_norm_lim=9, non_static=non_static, batch_size=1,
								dropout_rate=[0.5])
		

		#acc = np.sum(y_test == y_prediction, axis = 0) * 100/float(len(y_test))
		#print 'Test Accuracy ' + ' : ' + str(acc) + ' %'
		print y_prediction

