from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import tensorflow as tf
import tensorflow_hub as hub

layers = tf.keras.layers
_BATCH_NORM_DECAY = 0.997
_BATCH_NORM_EPSILON = 1e-5
DEFAULT_DTYPE = tf.float32


################################################################################
# Convenience functions for building the ResNet model.
################################################################################
def batch_norm(inputs, training, data_format):
    """Performs a batch normalization using a standard set of parameters."""
    # We set fused=True for a significant performance boost. See
    # https://www.tensorflow.org/performance/performance_guide#common_fused_ops
    return tf.layers.batch_normalization (
        inputs=inputs, axis=1 if data_format == 'channels_first' else 3,
        momentum=_BATCH_NORM_DECAY, epsilon=_BATCH_NORM_EPSILON, center=True,
        scale=True, training=training, fused=True)


def fixed_padding(inputs, kernel_size, data_format):
    """
      Pads the input along the spatial dimensions independently of input size.

      Args:
        inputs: A tensor of size [batch, channels, height_in, width_in] or
          [batch, height_in, width_in, channels] depending on data_format.
        kernel_size: The kernel to be used in the conv2d or max_pool2d operation.
                     Should be a positive integer.
        data_format: The input format ('channels_last' or 'channels_first').

      Returns:
        A tensor with the same format as the input with the data either intact
        (if kernel_size == 1) or padded (if kernel_size > 1).
      """
    pad_total = kernel_size - 1
    pad_beg = pad_total // 2
    pad_end = pad_total - pad_beg
    padded_inputs = layers.ZeroPadding2D((pad_beg, pad_end),
                                         data_format=data_format)(inputs)
    return padded_inputs


def conv2d_fixed_padding(inputs, filters, kernel_size, strides, data_format):
    """
  Strided 2-D convolution with explicit padding.
  The padding is consistent and is based only on `kernel_size`, not on the
  dimensions of `inputs` (as opposed to using `tf.layers.conv2d` alone).
  """
    if strides > 1:
        inputs = fixed_padding(inputs, kernel_size, data_format)

    return layers.Conv2D(filters=filters, kernel_size=kernel_size, strides=strides,
                         padding=('SAME' if strides == 1 else 'VALID'), use_bias=False,
                         kernel_initializer="he_uniform",
                         data_format=data_format)(inputs)


def _building_block_v2(inputs, filters, training, projection_shortcut, strides,
                       data_format):
    """A single block for ResNet v2, without a bottleneck.

      Batch normalization then ReLu then convolution as described by:
        Identity Mappings in Deep Residual Networks
        https://arxiv.org/pdf/1603.05027.pdf
        by Kaiming He, Xiangyu Zhang, Shaoqing Ren, and Jian Sun, Jul 2016.

      Args:
        inputs: A tensor of size [batch, channels, height_in, width_in] or
          [batch, height_in, width_in, channels] depending on data_format.
        filters: The number of filters for the convolutions.
        training: A Boolean for whether the model is in training or inference
          mode. Needed for batch normalization.
        projection_shortcut: The function to use for projection shortcuts
          (typically a 1x1 convolution when downsampling the input).
        strides: The block's stride. If greater than 1, this block will ultimately
          downsample the input.
        data_format: The input format ('channels_last' or 'channels_first').

      Returns:
        The output tensor of the block; shape should match inputs.
          input
        '    '
        '        '
        bn         '
        '          '
        '          '
        relu       '
        '          '
        '          '
        conv       (conv)
        '          '
        '          '
        bn         '
        '          '
        '          '
        relu       '
        '          '
        '          '
        conv       '
        '       '
        '   '
        +
    """
    shortcut = inputs
    inputs = batch_norm(inputs, training, data_format)
    inputs = layers.ReLU()(inputs)

    # The projection shortcut should come after the first batch norm and ReLU
    # since it performs a 1x1 convolution.
    if projection_shortcut is not None:
        shortcut = projection_shortcut(inputs)

    inputs = conv2d_fixed_padding(
        inputs=inputs, filters=filters, kernel_size=3, strides=strides,
        data_format=data_format)

    inputs = batch_norm(inputs, training, data_format)
    inputs = layers.ReLU()(inputs)
    inputs = conv2d_fixed_padding(
        inputs=inputs, filters=filters, kernel_size=3, strides=1,
        data_format=data_format)

    return inputs + shortcut


def _bottleneck_block_v2(inputs, filters, training, projection_shortcut,
                         strides, data_format):
    """A single block for ResNet v2, without a bottleneck.

          Similar to _building_block_v2(), except using the "bottleneck" blocks
          described in:
            Convolution then batch normalization then ReLU as described by:
              Deep Residual Learning for Image Recognition
              https://arxiv.org/pdf/1512.03385.pdf
              by Kaiming He, Xiangyu Zhang, Shaoqing Ren, and Jian Sun, Dec 2015.

          Adapted to the ordering conventions of:
            Batch normalization then ReLu then convolution as described by:
              Identity Mappings in Deep Residual Networks
              https://arxiv.org/pdf/1603.05027.pdf
              by Kaiming He, Xiangyu Zhang, Shaoqing Ren, and Jian Sun, Jul 2016.

          Args:
            inputs: A tensor of size [batch, channels, height_in, width_in] or
              [batch, height_in, width_in, channels] depending on data_format.
            filters: The number of filters for the convolutions.
            training: A Boolean for whether the model is in training or inference
              mode. Needed for batch normalization.
            projection_shortcut: The function to use for projection shortcuts
              (typically a 1x1 convolution when downsampling the input).
            strides: The block's stride. If greater than 1, this block will ultimately
              downsample the input.
            data_format: The input format ('channels_last' or 'channels_first').

          Returns:
            The output tensor of the block; shape should match inputs.
          """
    shortcut = inputs
    inputs = batch_norm(inputs, training, data_format)
    inputs = layers.ReLU()(inputs)

    # The projection shortcut should come after the first batch norm and ReLU
    # since it performs a 1x1 convolution.
    if projection_shortcut is not None:
        shortcut = projection_shortcut(inputs)

    inputs = conv2d_fixed_padding(
        inputs=inputs, filters=filters, kernel_size=1, strides=1,
        data_format=data_format)

    inputs = batch_norm(inputs, training, data_format)
    inputs = layers.ReLU()(inputs)
    inputs = conv2d_fixed_padding(
        inputs=inputs, filters=filters, kernel_size=3, strides=strides,
        data_format=data_format)

    inputs = batch_norm(inputs, training, data_format)
    inputs = layers.ReLU()(inputs)
    inputs = conv2d_fixed_padding(
        inputs=inputs, filters=4 * filters, kernel_size=1, strides=1,
        data_format=data_format)

    return inputs + shortcut


def block_layer(inputs, filters, bottleneck, block_fn, blocks, strides,
                training, name, data_format):
    """Creates one layer of blocks for the ResNet model.

          Args:
            inputs: A tensor of size [batch, channels, height_in, width_in] or
              [batch, height_in, width_in, channels] depending on data_format.
            filters: The number of filters for the first convolution of the layer.
            bottleneck: Is the block created a bottleneck block.
            block_fn: The block to use within the model, either `building_block` or
              `bottleneck_block`.
            blocks: The number of blocks contained in the layer.
            strides: The stride to use for the first convolution of the layer. If
              greater than 1, this layer will ultimately downsample the input.
            training: Either True or False, whether we are currently training the
              model. Needed for batch norm.
            name: A string name for the tensor output of the block layer.
            data_format: The input format ('channels_last' or 'channels_first').

          Returns:
            The output tensor of the block layer.
          """

    # Bottleneck blocks end with 4x the number of filters as they start with
    filters_out = filters * 4 if bottleneck else filters

    def projection_shortcut(inputs):
        return conv2d_fixed_padding(
            inputs=inputs, filters=filters_out, kernel_size=1, strides=strides,
            data_format=data_format)

    # Only the first block per block_layer uses projection_shortcut and strides
    inputs = block_fn(inputs, filters, training, projection_shortcut, strides,
                      data_format)

    for _ in range(1, blocks):
        inputs = block_fn(inputs, filters, training, None, 1, data_format)

    return tf.identity(inputs, name)


def resnet_v2(inputs, training, reuse=tf.AUTO_REUSE, data_format="channels_last"):
    """Add operations to classify a batch of input images.

    Args:
      inputs: A Tensor representing a batch of input images.
      training: A boolean. Set to True to add operations required only when
        training the classifier.

    Returns:
      A logits Tensor with shape [<batch_size>, self.num_classes].
    """
    with tf.variable_scope('resnet_model', reuse=reuse):
        inputs = conv2d_fixed_padding(
            inputs=inputs, filters=64, kernel_size=7,
            strides=2, data_format=data_format)
        inputs = tf.identity(inputs, 'initial_conv')

        # We do not include batch normalization or activation functions in V2
        # for the initial conv1 because the first ResNet unit will perform these
        # for both the shortcut and non-shortcut paths as part of the first
        # block's projection. Cf. Appendix of [2].
        inputs = layers.MaxPool2D(pool_size=3,
                                  strides=2, padding='SAME',
                                  data_format=data_format)(inputs)
        inputs = tf.identity(inputs, 'initial_max_pool')
        block_strides = [1, 2, 2, 2]
        image_feature_map = {}
        for i, num_blocks in enumerate([3, 4, 6, 3]):
            num_filters = 64 * (2 ** i)
            inputs = block_layer(
                inputs=inputs, filters=num_filters, bottleneck=True,
                block_fn=_bottleneck_block_v2, blocks=num_blocks,
                strides=block_strides[i], training=training,
                name='block_layer{}'.format(i + 1), data_format=data_format)
            image_feature_map["C%d" % (i + 2)] = inputs

        return image_feature_map