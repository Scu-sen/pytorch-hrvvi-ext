import torch
import torch.nn as nn

from collections import OrderedDict
import numpy as np


def summary(model, input_size, batch_size=-1, dtype=None):

    visited = set()

    def register_hook(module):

        def hook(module, input, output):
            class_name = str(module.__class__).split(".")[-1].split("'")[0]
            module_idx = len(summary)

            m_key = "%s-%i" % (class_name, module_idx + 1)

            summary[m_key] = OrderedDict()
            summary[m_key]["input_shape"] = list(input[0].size())
            summary[m_key]["input_shape"][0] = batch_size
            if isinstance(output, (list, tuple)):
                if isinstance(output[0], (list, tuple)):
                    summary[m_key]["output_shape"] = [
                        [-1] + list(o.size())[1:]
                        for os in output
                        for o in os
                    ]
                else:
                    output_shape = []
                    for o in output:
                        if torch.is_tensor(o):
                            output_shape.append([-1] + list(o.size())[1:])
                    summary[m_key]["output_shape"] = output_shape
            else:
                summary[m_key]["output_shape"] = list(output.size())
                summary[m_key]["output_shape"][0] = batch_size

            if module not in visited:
                params = 0
                if hasattr(module, "weight") and hasattr(module.weight, "size"):
                    params += module.weight.size().numel()
                    summary[m_key]["trainable"] = module.weight.requires_grad
                if hasattr(module, "bias") and hasattr(module.bias, "size"):
                    params += module.bias.size().numel()
                summary[m_key]["nb_params"] = params
                visited.add(module)
            else:
                summary[m_key]["nb_params"] = 0

        if (
            not isinstance(module, nn.Sequential)
            and not isinstance(module, nn.ModuleList)
            and not (module == model)
        ):
            hooks.append(module.register_forward_hook(hook))

    dtype = dtype or torch.float32

    device = model.parameters().__next__().device.type
    if device != 'cpu':
        if dtype == torch.float32:
            dtype = torch.cuda.FloatTensor
        elif dtype == torch.long:
            dtype = torch.cuda.LongTensor

    # multiple inputs to the network
    if isinstance(input_size, tuple):
        input_size = [input_size]

    # batch_size of 2 for batchnorm
    x = [torch.rand(2, *in_size).type(dtype) for in_size in input_size]

    # create properties
    summary = OrderedDict()
    hooks = []

    # register hook
    model.apply(register_hook)

    # make a forward pass
    # print(x.shape)
    model(*x)

    # remove these hooks
    for h in hooks:
        h.remove()

    print("----------------------------------------------------------------")
    line_new = "{:>20}  {:>25} {:>15}".format(
        "Layer (type)", "Output Shape", "Param #")
    print(line_new)
    print("================================================================")
    total_params = 0
    total_output = 0
    trainable_params = 0
    for layer in summary:
        # input_shape, output_shape, trainable, nb_params
        output_shape = summary[layer]["output_shape"]
        if isinstance(output_shape[0], list):
            line_new = "{:>20}  {:>25} {:>15}".format(
                layer,
                str(output_shape[0]),
                "{0:,}".format(summary[layer]["nb_params"]),
            )
            print(line_new)
            for shape in output_shape[1:]:
                line_new = "{:>20}  {:>25} {:>15}".format(
                    "", str(shape), "")
                print(line_new)
        else:
            line_new = "{:>20}  {:>25} {:>15}".format(
                layer,
                str(summary[layer]["output_shape"]),
                "{0:,}".format(summary[layer]["nb_params"]),
            )
            print(line_new)
        total_params += summary[layer]["nb_params"]
        if isinstance(output_shape[0], list):
            total_output += np.sum([np.prod(out) for out in output_shape])
        else:
            total_output += np.prod(summary[layer]["output_shape"])
        if "trainable" in summary[layer]:
            if summary[layer]["trainable"] == True:
                trainable_params += summary[layer]["nb_params"]

    # assume 4 bytes/number (float on cuda).
    total_input_size = sum([abs(np.prod(size) *
                           batch_size * 4. / (1024 ** 2.)) for size in input_size])
    total_output_size = abs(2. * total_output * 4. /
                            (1024 ** 2.))  # x2 for gradients
    total_params_size = abs(total_params * 4. / (1024 ** 2.))
    total_size = total_params_size + total_output_size + total_input_size

    print("================================================================")
    print("Total params: {0:,}".format(total_params))
    print("Trainable params: {0:,}".format(trainable_params))
    print(
        "Non-trainable params: {0:,}".format(total_params - trainable_params))
    print("----------------------------------------------------------------")
    print("Input size (MB): %0.2f" % total_input_size)
    print("Forward/backward pass size (MB): %0.2f" % total_output_size)
    print("Params size (MB): %0.2f" % total_params_size)
    print("Estimated Total Size (MB): %0.2f" % total_size)
    print("----------------------------------------------------------------")
    # return summary
