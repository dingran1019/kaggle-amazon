import numpy as np
import pickle
import pandas as pd
import glob
import os
import gc
from tag_translation import train_label, tags_to_vec, map_predictions

from skimage import io
from skimage.transform import resize
from sklearn.model_selection import train_test_split
from sklearn.metrics import fbeta_score
from tqdm import tqdm
from numpy.random import shuffle

import keras
from keras.models import load_model
from keras.applications.inception_v3 import InceptionV3
from keras.applications.resnet50 import ResNet50
from keras.preprocessing import image
from keras.models import Model
from keras.layers import Dense, GlobalAveragePooling2D, Dropout
from keras import backend as K
from keras.callbacks import ModelCheckpoint, Callback, EarlyStopping
from keras.preprocessing.image import ImageDataGenerator

dir_path = os.path.dirname(os.path.realpath(__file__))
code_dir = dir_path
data_dir = os.path.join(dir_path, '../input')
print(data_dir)

file_type = 'jpg'
file_folder = 'train-jpg'
file_type_file = os.path.join(code_dir, 'file_type.txt')
if os.path.exists(file_type_file):
    with open(file_type_file, 'r') as f:
        type_str = f.readline()
        print(type_str)
    if type_str == 'tif' or type_str.startswith('t'):
        file_type = 'tif'
        file_folder = 'train-tif-v2'
print('file type: ', file_type)

model_type = 'inception'
model_type_file = os.path.join(code_dir, 'model_type.txt')

if os.path.exists(model_type_file):
    with open(model_type_file, 'r') as f:
        model_type = f.readline()
        print(model_type)

if not model_type.startswith('i'):
    print('using ResNet50')
    keras_model = ResNet50
    image_shape = (224, 224)
    n_traiable_layers = 10
else:
    print('using InceptionV3')
    keras_model = InceptionV3
    image_shape = (299, 299)
    n_traiable_layers = 10

resuming = True
new_learning_rate = None
new_learning_rate = 0.0001
do_training = True
N_train_limit = int(2e9)

if 1:
    N_sample = min(N_train_limit, train_label.shape[0])
    X_train = np.empty([N_sample, image_shape[0], image_shape[1], 3], dtype='float32')
    y_train = np.empty([N_sample, 17], dtype='float32')
    filename_list = []
    i = 0
    for idx, row in tqdm(train_label.iterrows(), total=N_sample):
        image = io.imread(
            os.path.join(data_dir, file_folder, '{}.{}'.format(row['image_name'], file_type)))
        image = resize(image, image_shape, mode='constant')  # for InceptionV3
        if file_type == 'tif':
            X_train[i, :, :, :] = image[:, :, [0, 1, 3]]
        else:
            X_train[i, :, :, :] = image
        y_train[i, :] = row['y']
        filename_list.append(row['image_name'])
        i += 1
        if i == N_train_limit:
            break

    # X_train = np.stack(X_train, axis=0)
    # y_train = np.stack(y_train, axis=0)
    print(X_train.shape, y_train.shape)
    print(X_train.dtype, y_train.dtype)

    rand_idx = np.arange(0, N_sample)
    shuffle(rand_idx)

    N_train = int(0.8 * N_sample)

    filename_list = np.array(filename_list)

    xtrain = X_train[rand_idx[:N_train]]
    ytrain = y_train[rand_idx[:N_train]]
    fname_train = filename_list[rand_idx[:N_train]]

    xvalid = X_train[rand_idx[N_train:]]
    yvalid = y_train[rand_idx[N_train:]]
    fname_valid = filename_list[rand_idx[N_train:]]
    # xtrain, xvalid, ytrain, yvalid = train_test_split(X_train, y_train, test_size=0.2)
    print(xtrain.shape, xvalid.shape, ytrain.shape, yvalid.shape)

else:

    def assemble_batch(sub_list):
        print('sublist length ', len(sub_list))
        # X_test = np.empty([len(test_file_list), 299, 299, 3])
        X_test = np.empty([len(sub_list), image_shape[0], image_shape[1], 3], dtype='float32')
        test_filenames = []
        i = 0
        for t in tqdm(sub_list):
            filename = os.path.basename(t).replace('.{}'.format(file_type), '')
            test_filenames.append(filename)
            image = io.imread(t)
            image = resize(image, image_shape, mode='constant')  # for InceptionV3
            if image.shape[-1] == 4:
                X_test[i, :, :, :] = image[:, :, [0, 1, 3]]
            else:
                X_test[i, :, :, :] = image
            i += 1

        # X_test = np.stack(X_test, axis=0)
        print(X_test.shape)
        print(X_test.dtype)

        return X_test, test_filenames


    test_file_list = glob.glob(os.path.join(data_dir, '{}/*.{}'.format(file_folder, file_type)))
    N_sample = min(N_train_limit, len(test_file_list))

    X_test, test_filenames = assemble_batch(test_file_list[:N_sample])
    ytest = []
    for t in test_filenames:
        ytmp = train_label.loc[train_label['image_name'] == t]['y'].values[0]
        ytest.append(ytmp)

    ytest = np.stack(ytest, axis=0)

    rand_idx = np.arange(0, N_sample)
    shuffle(rand_idx)

    N_train = int(0.8 * N_sample)

    xtrain = X_test[rand_idx[:N_train]]
    ytrain = ytest[rand_idx[:N_train]]

    xvalid = X_test[rand_idx[N_train:]]
    yvalid = ytest[rand_idx[N_train:]]
    # xtrain, xvalid, ytrain, yvalid = train_test_split(X_train, y_train, test_size=0.2)
    print(xtrain.shape, xvalid.shape, ytrain.shape, yvalid.shape)

try:
    del X_train, y_train
except:
    pass
gc.collect()

# xtrain = xtrain.astype('float32')
# xvalid = xvalid.astype('float32')
xtrain /= 255
xvalid /= 255

model = None
# raw predictions for optimizing thresholds later
model_paths = glob.glob(os.path.join(code_dir, 'model*.hdf5'))

if resuming:
    if model_paths:
        model_path = min(model_paths)
        print('loading ', model_path)
        model_name = os.path.basename(model_path).replace('.hdf5', '')
        model = load_model(model_path)
        raw_prediction_filename = os.path.join(code_dir, 'raw_pred_{}.pkl'.format(model_name))

        if not os.path.exists(raw_prediction_filename):
            print('{} does not exist, now generating it'.format(raw_prediction_filename))
            ypred_train = model.predict(xtrain, verbose=1)
            ypred_valid = model.predict(xvalid, verbose=1)

            # a quick check on score
            y_pred_i = (ypred_train > 0.2).astype(int)
            score = fbeta_score(ytrain, y_pred_i, beta=2, average='samples')
            print('fbeta score on validation set: {}'.format(score))

            y_pred_i = (ypred_valid > 0.2).astype(int)
            score = fbeta_score(yvalid, y_pred_i, beta=2, average='samples')
            print('fbeta score on validation set: {}'.format(score))

            with open(raw_prediction_filename, 'wb') as f:
                pickle.dump((ypred_train, ypred_valid, ytrain, yvalid), f)
    else:
        print('no model available, abort')
        resuming = False

if do_training:
    batch_size = 32
    num_classes = 17
    epochs = 200
    data_augmentation = True

    if not resuming:  # create fresh model
        print('creating fresh model')
        # create the base pre-trained model
        base_model = keras_model(weights='imagenet', include_top=False)

        # add a global spatial average pooling layer
        x = base_model.output
        x = GlobalAveragePooling2D()(x)
        # let's add a fully-connected layer
        x = Dense(1024, activation='relu')(x)
        x = Dropout(0.5)(x)
        x = Dense(1024, activation="relu")(x)
        predictions = Dense(17, activation='sigmoid')(x)

        # this is the model we will train
        model = Model(inputs=base_model.input, outputs=predictions)
        N_layers = len(model.layers)
        print('model has {} layers'.format(N_layers))

        # first: train only the top layers (which were randomly initialized)
        # i.e. freeze all convolutional InceptionV3 layers
        # for layer in base_model.layers:
        #     layer.trainable = False

        N_last = min(N_layers, n_traiable_layers)
        print('setting last {} layers to be trainable'.format(N_last))
        for layer in model.layers:
            layer.trainable = False
        for layer in model.layers[-N_last:]:
            layer.trainable = True

        # compile the model (should be done *after* setting layers to non-trainable)
        adam_opt = keras.optimizers.Adam(lr=0.001, decay=1e-6)
        model.compile(optimizer=adam_opt, loss='binary_crossentropy')

    else:
        print('resuming with loaded model')

        N_layers = len(model.layers)
        print('model has {} layers'.format(N_layers))

        N_last = min(N_layers, n_traiable_layers)
        print('setting last {} layers to be trainable'.format(N_last))
        for layer in model.layers:
            layer.trainable = False
        for layer in model.layers[-N_last:]:
            layer.trainable = True

        if new_learning_rate is not None:  # not sure if this works
            print('compiling model with new learning rate {}'.format(new_learning_rate))
            adam_opt = keras.optimizers.Adam(lr=new_learning_rate, decay=1e-6)
            model.compile(optimizer=adam_opt, loss='binary_crossentropy')


    # defining a set of callbacks
    class f2beta(Callback):
        def __init__(self, xval, yval):
            self.xval = xval
            self.yval = yval
            self.maps = []

        def eval_map(self):
            x_val = self.xval
            y_true = self.yval
            y_pred = self.model.predict(x_val)

            y_pred = (y_pred > 0.2).astype(int)
            score = fbeta_score(y_true, y_pred, beta=2, average='samples')

            return score

        def on_epoch_end(self, epoch, logs={}):
            score = self.eval_map()
            print("f2beta for epoch %d is %f" % (epoch, score))
            self.maps.append(score)


    beta_score = f2beta(xvalid, yvalid)

    checkpoint = ModelCheckpoint(os.path.join(code_dir, "model-{val_loss:.5f}.hdf5"),
                                 monitor='val_loss', verbose=1, save_best_only=True, mode='auto')

    earlystop = EarlyStopping(monitor='val_loss', min_delta=0, patience=10, verbose=0, mode='auto')

    if not data_augmentation:
        print('Not using data augmentation.')
        model.fit(xtrain, ytrain,
                  batch_size=batch_size,
                  epochs=epochs, callbacks=[checkpoint, beta_score, earlystop],
                  validation_data=(xvalid, yvalid),
                  shuffle=True)
    else:
        print('Using real-time data augmentation.')
        # This will do preprocessing and realtime data augmentation:
        datagen = ImageDataGenerator(
            featurewise_center=False,  # set input mean to 0 over the dataset
            samplewise_center=False,  # set each sample mean to 0
            featurewise_std_normalization=False,  # divide inputs by std of the dataset
            samplewise_std_normalization=False,  # divide each input by its std
            zca_whitening=False,  # apply ZCA whitening
            rotation_range=359,  # randomly rotate images in the range (degrees, 0 to 180)
            width_shift_range=0.3,  # randomly shift images horizontally (fraction of total width)
            height_shift_range=0.3,  # randomly shift images vertically (fraction of total height)
            horizontal_flip=True,  # randomly flip images
            vertical_flip=True)  # randomly flip images

        # Compute quantities required for feature-wise normalization
        # (std, mean, and principal components if ZCA whitening is applied).
        datagen.fit(xtrain)

        # Fit the model on the batches generated by datagen.flow().
        model.fit_generator(datagen.flow(xtrain, ytrain, batch_size=batch_size),
                            steps_per_epoch=xtrain.shape[0] // batch_size,
                            epochs=epochs, callbacks=[checkpoint, beta_score, earlystop],
                            validation_data=(xvalid, yvalid))

ypred_train = model.predict(xtrain, verbose=1)
ypred_valid = model.predict(xvalid, verbose=1)

# a quick check on score
y_pred_i = (ypred_train > 0.2).astype(int)
score = fbeta_score(ytrain, y_pred_i, beta=2, average='samples')
print('fbeta score on validation set: {}'.format(score))

y_pred_i = (ypred_valid > 0.2).astype(int)
score = fbeta_score(yvalid, y_pred_i, beta=2, average='samples')
print('fbeta score on validation set: {}'.format(score))

model_paths = glob.glob(os.path.join(code_dir, 'model*.hdf5'))
if model_paths:
    model_path = min(model_paths)
    print('loading ', model_path)
    model_name = os.path.basename(model_path).replace('.hdf5', '')
else:
    print('no model available, abort')
    assert 0

raw_prediction_filename = os.path.join(code_dir, 'raw_pred_{}.pkl'.format(model_name))

with open(raw_prediction_filename, 'wb') as f:
    pickle.dump((ypred_train, ypred_valid, ytrain, yvalid), f)
