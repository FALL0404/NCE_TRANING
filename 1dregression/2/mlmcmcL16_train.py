# camera-ready

from datasets import ToyDataset # (this needs to be imported before torch, because cv2 needs to be imported before torch for some reason)
from model import ToyNet

import torch
import torch.utils.data
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F

import torch.distributions

import math
import numpy as np
import pickle
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import cv2

# NOTE! change this to not overwrite all log data when you train the model:
model_id = "2-mlmcmcL16"

num_epochs = 75
batch_size = 32
learning_rate = 0.001

num_samples = 1024
print (num_samples)

L = 16
lambd = 0.0125
print (lambd)
print (L)

###########################
import math
def gauss_density_centered(x, std):
    return torch.exp(-0.5*(x / std)**2) / (math.sqrt(2*math.pi)*std)

def gmm_density_centered(x, std):
    """
    Assumes dim=-1 is the component dimension and dim=-2 is feature dimension. Rest are sample dimension.
    """
    if x.dim() == std.dim() - 1:
        x = x.unsqueeze(-1)
    elif not (x.dim() == std.dim() and x.shape[-1] == 1):
        raise ValueError('Last dimension must be the gmm stds.')
    return gauss_density_centered(x, std).prod(-2).mean(-1)

def sample_gmm_centered(std, num_samples=1):
    num_components = std.shape[-1]
    num_dims = std.numel() // num_components

    std = std.view(1, num_dims, num_components)

    # Sample component ids
    k = torch.randint(num_components, (num_samples,), dtype=torch.int64)
    std_samp = std[0,:,k].t()

    # Sample
    x_centered = std_samp * torch.randn(num_samples, num_dims)
    prob_dens = gmm_density_centered(x_centered, std)

    return x_centered, prob_dens

stds = torch.zeros((1, 1))
stds[0, 0] = 1.0e-30
print (stds)
###########################

train_dataset = ToyDataset()

num_train_batches = int(len(train_dataset)/batch_size)
print ("num_train_batches:", num_train_batches)

train_loader = torch.utils.data.DataLoader(dataset=train_dataset, batch_size=batch_size, shuffle=True)

num_models = 20
for i in range(num_models):
    network = ToyNet(model_id + "_%d" % i, project_dir="/root/ebms_regression/1dregression").cuda()

    optimizer = torch.optim.Adam(network.parameters(), lr=learning_rate)

    epoch_losses_train = []
    for epoch in range(num_epochs):
        print ("###########################")
        print ("######## NEW EPOCH ########")
        print ("###########################")
        print ("model: %d/%d  |  epoch: %d/%d" % (i+1, num_models, epoch+1, num_epochs))

        network.train() # (set in training mode, this affects BatchNorm and dropout)
        batch_losses = []
        for step, (xs, ys) in enumerate(train_loader):
            xs = xs.cuda().unsqueeze(1) # (shape: (batch_size, 1))
            ys = ys.cuda().unsqueeze(1) # (shape: (batch_size, 1))

            x_features = network.feature_net(xs) # (shape: (batch_size, hidden_dim))
            fs = network.predictor_net(x_features, ys) # (shape: (batch_size, 1))
            fs = fs.squeeze(1) # (shape: (batch_size))

            y_samples_zero, _ = sample_gmm_centered(stds, num_samples=num_samples)
            y_samples_zero = y_samples_zero.cuda() # (shape: (num_samples, 1))
            y_samples_zero = y_samples_zero.squeeze(1) # (shape: (num_samples))
            y_samples = ys + y_samples_zero.unsqueeze(0) # (shape: (batch_size, num_samples))
            y_samples = y_samples.reshape(-1).unsqueeze(1) # (shape: (batch_size*num_samples, 1))

            x_features = x_features.view(xs.size(0), 1, -1).expand(-1, num_samples, -1) # (shape: (batch_size, num_samples, hidden_dim))
            x_features = x_features.reshape(xs.size(0)*num_samples, -1) # (shape: (batch_size*num_samples, hidden_dim))

            ########################################################################
            # compute loss:
            ########################################################################
            for l in range(L):
                y_samples.requires_grad_(True)

                f_samples = network.predictor_net(x_features, y_samples) # (shape: (batch_size*num_samples, 1))
                f_samples = f_samples.squeeze(1) # (shape: (batch_size*num_samples))

                grad_y_f_samples = torch.autograd.grad(f_samples.sum(), y_samples, create_graph=True)[0]
                # (shape: (batch_size*num_samples, 1)) (like y_samples)

                y_samples = y_samples + (0.5*lambd**2)*grad_y_f_samples + lambd*torch.randn(y_samples.size()).cuda()

                y_samples.detach_()

            f_samples = network.predictor_net(x_features, y_samples) # (shape: (batch_size*num_samples, 1))
            f_samples = f_samples.squeeze(1) # (shape: (batch_size*num_samples))
            f_samples = f_samples.reshape(xs.size(0), num_samples) # (shape: (batch_size, num_samples))

            loss = torch.mean(torch.mean(f_samples, dim=1) - fs)

            loss_value = loss.data.cpu().numpy()
            batch_losses.append(loss_value)

            ########################################################################
            # optimization step:
            ########################################################################
            optimizer.zero_grad() # (reset gradients)
            loss.backward() # (compute gradients)
            optimizer.step() # (perform optimization step)

        print("max_score_samp = {} ,  max_score = {}".format(f_samples.max().item(), fs.max().item()))

        epoch_loss = np.mean(batch_losses)
        epoch_losses_train.append(epoch_loss)
        with open("%s/epoch_losses_train.pkl" % network.model_dir, "wb") as file:
            pickle.dump(epoch_losses_train, file)
        print ("train loss: %g" % epoch_loss)
        plt.figure(1)
        plt.plot(epoch_losses_train, "k^")
        plt.plot(epoch_losses_train, "k")
        plt.ylabel("loss")
        plt.xlabel("epoch")
        plt.title("train loss per epoch")
        plt.savefig("%s/epoch_losses_train.png" % network.model_dir)
        plt.close(1)

        # save the model weights to disk:
        checkpoint_path = network.checkpoints_dir + "/model_" + model_id +"_epoch_" + str(epoch+1) + ".pth"
        torch.save(network.state_dict(), checkpoint_path)
