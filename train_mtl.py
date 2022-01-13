import tensorflow as tf
from keras.callbacks import CSVLogger
from tensorflow import keras
import numpy as np
import matplotlib.pyplot as plt

from utils.loader import DataLoader
from models.effnet_encoder import EffnetEncoder
from models.mtl_framework import MTLFramework
from utils import tools, config


# Set configs
from utils.unet_helper_functions import get_mtl_training_log_path

batch_size = 16
batch_size_val = 16
num_train, num_val, num_test = config.config['num_train'], config.config['num_val'], config.config['num_test']
img_height, img_width, channels = config.config['input_shape']

# Load our data pipeline
loader = DataLoader(batch_size=batch_size, batch_size_val=batch_size_val, CrossVal=0, CV_iteration=0)

# Train set
img_ds = loader.get_image_ds().repeat()
masks_ds = loader.get_mask_ds().repeat()
label_ds = loader.get_binary_ds().repeat()
bbox_ds = loader.get_bboxes_ds().repeat()

# Validation set
img_ds_val = loader.get_image_ds(val=True).repeat()
masks_ds_val = loader.get_mask_ds(val=True).repeat()
label_ds_val = loader.get_binary_ds(val=True).repeat()
bbox_ds_val = loader.get_bboxes_ds(val=True).repeat()


### CLEARS OLD MODELS IN CACHE
tf.keras.backend.clear_session()

# Get encoder
base_model_name = 'B0'
encoder = EffnetEncoder(base_model_name, (img_height, img_width, channels)).build_encoder(trainable=True)
encoder.summary()

# Use our MTL framework to custom build a model
mtl_builder = MTLFramework(encoder, (img_height, img_width, channels))
mtl_builder.add_segmentation_head()
mtl_builder.add_binary_classification_head(base_model_name, trainable=True)
mtl_builder.add_bbox_classification_head(base_model_name, trainable=True)
model = mtl_builder.build_mtl_model()
model.summary()


def generator_img():
    ''' Merges together datasets into a unified generator to pass for training '''
    # We use generators like this to abstract shuffling away from the loader and customise yielding
    # directly from the loaders like shuffling and augmentation.
    a = img_ds.as_numpy_iterator()
    b = masks_ds.as_numpy_iterator()
    c = label_ds.as_numpy_iterator()
    d = bbox_ds.as_numpy_iterator()

    while True:
        X = a.next()
        Y1 = b.next()
        Y2 = c.next()
        Y3 = d.next()

        # Regularisation and shuffling
        X, Y1, Y2, Y3 = tools.get_randomised_data([X, Y1, Y2, Y3])
        X, Y1, Y3 = tools.data_augmentation(X, Y1, Y3)  # Fix augmentation

        yield X, (Y1, Y2, Y3)





def generator_img_val():
    ''' Merges together datasets into a unified generator to pass for training '''
    a = img_ds_val.as_numpy_iterator()
    b = masks_ds_val.as_numpy_iterator()
    c = label_ds_val.as_numpy_iterator()
    d = bbox_ds.as_numpy_iterator()

    while True:
        X = a.next()
        Y1 = b.next()
        Y2 = c.next()
        Y3 = d.next()

        yield X, (Y1, Y2, Y3)

# Initial train
model.compile(optimizer=keras.optimizers.Adam(),
              loss={'segnet_out': tf.keras.losses.BinaryCrossentropy(from_logits=True),
                    'bin_class_out': tf.keras.losses.BinaryCrossentropy(),
                    'bbox_out': tf.keras.losses.MeanAbsoluteError()},
              loss_weights=[1, 1, 1 / 100],  # Scale MAE to BC range
              metrics=['accuracy'])

csv_logger = CSVLogger(get_mtl_training_log_path(val=False), separator=',', append=False)

history = model.fit(generator_img(), validation_data=generator_img_val(), epochs=15,
                    steps_per_epoch=num_train // batch_size, validation_steps=num_val // batch_size_val, callbacks=[csv_logger],)

# Fine-tuning at lower learning rate
model.compile(optimizer=keras.optimizers.Adam(1e-4),
              loss={'segnet_out': tf.keras.losses.BinaryCrossentropy(from_logits=True),
                    'bin_class_out': tf.keras.losses.BinaryCrossentropy(),
                    'bbox_out': tf.keras.losses.MeanAbsoluteError()},
              loss_weights=[1, 1, 1 / 100],  # Scale MAE to BC range
              metrics=['accuracy'])

csv_logger = CSVLogger(get_mtl_training_log_path(val=True), separator=',', append=False)

history = model.fit(generator_img(), validation_data=generator_img_val(), epochs=10,
                    steps_per_epoch=num_train // batch_size, validation_steps=num_val // batch_size_val, callbacks=[csv_logger],)
fig, ax = plt.subplots(figsize=(8, 6))
ax.plot(list(range(10)), history.history['segnet_out_accuracy'], 'r-', label='Segmentation - Training Accuracy')
ax.plot(list(range(10)), history.history['val_segnet_out_accuracy'], 'r--', label='Segmentation - Validation Accuracy')
ax.plot(list(range(10)), history.history['bin_class_out_accuracy'], 'c-', label='Classification - Training Accuracy')
ax.plot(list(range(10)), history.history['val_bin_class_out_accuracy'], 'c--',
        label='Classification - Validation Accuracy')
ax2 = ax.twinx()
ax.plot(list(range(10)), history.history['bbox_out_accuracy'], 'm-', label='Bounding Box - Training Accuracy')
ax.plot(list(range(10)), history.history['val_bbox_out_accuracy'], 'm--', label='Bounding Box - Validation Accuracy')
ax.legend()
ax.set_xlabel('Epochs')
ax.set_ylabel('Segmentation/Classification Accuracy')
ax2.set_ylabel('Bounding Box Accuracy')

# model.save_model('model_weights/EffishingNetN')

# model = tf.keras.models.load_model('model_weights/EffishingNetN')

# # Load test-set
# img_ds_test = loader.get_image_ds(test_mode=True)
# masks_ds_test = loader.get_mask_ds(test_mode=True)
# label_ds_test = loader.get_binary_ds(test_mode=True)
# bbox_ds_test = loader.get_bboxes_ds(test_mode=True)
#
#
# # Predict on test-set
# seg_pred, bin_pred, bbox_pred = model.predict(img_ds_test, batch_size=10)
# seg_pred = tf.where(seg_pred >= 0, 1, 0)  # Convert to {0,1} binary classes
# bin_pred = np.round(bin_pred)  # Round confidence score
#
# bin_acc = np.sum(bin_pred == label_ds_test) / label_ds_test.shape[0]
# seg_acc = np.sum(seg_pred == masks_ds_test) / (masks_ds_test.shape[0] * (img_height * img_width))
# iou = np.mean(tools.calculate_iou(bbox_ds_test, bbox_pred))
# print(
#     f'Binary Acc: {round(bin_acc * 100, 3)}%,   Seg Acc: {round(seg_acc * 100, 3)}%,    BBox IOU: {round(iou * 100, 3)}%')
#
#
# # # Visualise predictions
# # idx = list(range(img_ds_test.shape[0]))
# # random.shuffle(idx)
# # for i in range(3):
# #     tools.show_seg_pred(img_ds_test[idx[i]], masks_ds_test[idx[i]], seg_pred[idx[i]][tf.newaxis, ...], bbox_ds_test[idx[i]], bbox_pred[idx[i]])
#
