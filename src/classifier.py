from tqdm import tqdm
import sys
import torch
import os
import numpy as np
import pandas as pd
import torch.nn.functional
from torch.utils.data import DataLoader
import sklearn
from sklearn.preprocessing import StandardScaler
import copy
import pickle
from sklearn.metrics import balanced_accuracy_score
import io
import matplotlib.pyplot as plt

# TODO: Read more about model weight initializations. Maybe I want to use something other than random? 
# TODO: Why bother scaling the loss function weights by the number of classes?
# TODO: Refresh my memory on cross-entropy loss. 
# TODO: Find out what the epsilon parameter of the optimizer does. Apparently it's just a small constant added for numerical stability, so there's no division by zero errors. 
#   I think to fully understand why it's necessary, I'll need to read the Adam paper https://arxiv.org/abs/1412.6980 

DEVICE = 'cuda' if torch.cuda.is_available() else 'cpu'

class Unpickler(pickle.Unpickler):
    # https://github.com/pytorch/pytorch/issues/16797
    def find_class(self, module, name):
        if module == 'torch.storage' and name == '_load_from_bytes':
            return lambda b: torch.load(io.BytesIO(b), weights_only=False, map_location='cpu')
        else: return super().find_class(module, name)


class WeightedCrossEntropyLoss(torch.nn.Module):

    def __init__(self, n_classes:int=2):

        super(WeightedCrossEntropyLoss, self).__init__()

        self.weights = torch.FloatTensor([1] * n_classes).to(DEVICE)
        self.to(DEVICE) # Not actually sure if this is necessary. 

    def fit(self, dataset):
        '''Compute the weights to use based on the inverse frequencies of each class. '''
        n_per_class = [(dataset.label == i).sum() for i in range(dataset.n_classes)]
        self.weights = torch.FloatTensor([(len(dataset) / (n_i * dataset.n_classes)) for n_i in n]).to(DEVICE)

    def forward(self, outputs, targets):
        '''Compute the weighted loss between the targets and outputs. 

        :param outputs: A Tensor of size (batch_size, n_classes). All values should be between 0 and 1, and sum to 1. 
        :param targets: A Tensor of size (batch_size, n_classes), which is a one-hot encoded vector of the labels.  
        '''
        outputs = outputs.view(targets.shape) # Make sure the outputs and targets have the same shape. Use view to avoid copying. 
        loss = torch.nn.functional.cross_entropy(outputs, targets, reduction='none') # Reduction specifies the pooling to apply to the output. If 'none', no reduction will be applied. 
        weights = torch.unsqueeze(self.weights, 0).repeat(len(outputs), 1)
        weights = (targets * weights).sum(axis=1)

        return (loss * weights).mean()


class Classifier(torch.nn.Module):

    def __init__(self, dims:tuple=(1024, 512, 2)):

        super(Classifier, self).__init__()
       
        self.dtype = torch.float32

        self.model = torch.nn.Sequential(
            torch.nn.Linear(dims[0], dims[1], dtype=self.dtype),
            torch.nn.ReLU(),
            torch.nn.Linear(dims[1], dims[2], dtype=self.dtype))

        self.loss_func = WeightedCrossEntropyLoss(n_classes=dims[2])
        self.scaler = StandardScaler()
        self.to(DEVICE)

        self.metrics = dict()
        self.metrics['train_loss'] = []
        self.metrics['test_acc'] = []

    def forward(self, inputs:torch.FloatTensor):
        return self.model(inputs) 

    def fitted(self):
        return hasattr(self, 'epochs')

    def predict(self, dataset, include_outputs:bool=False) -> pd.DataFrame:
 
        assert dataset.scaled, 'Classifier.predict: The input Dataset has not been scaled.'

        self.eval() # Put the model in evaluation mode. This changes the forward behavior of the model (e.g. disables dropout).
        with torch.no_grad(): # Turn off gradient computation, which reduces memory usage. 
            outputs = self(dataset.embedding) # Run a forward pass of the model.
            outputs = torch.nn.functional.softmax(outputs, 1) # Apply sigmoid activation, which is applied as a part of the loss function during training. 
            outputs = outputs.cpu().numpy()
        
        labels = np.argmax(outputs, axis=1).ravel() # Convert out of one-hot encodings.
        return (labels, outputs) if include_outputs else labels


    def accuracy(self, dataset) -> float:
        '''Compute the balanced accuracy of the model on the input dataset.'''
        labels = dataset.label # Get the non-one-hot encoded labels from the dataset. 
        return balanced_accuracy_score(labels, self.predict(dataset))
    
    def scale(self, dataset, fit:bool=True):
        # Repeatedly scaling a Dataset causes problems, though I am not sure why. I would have thought that
        # subsequent applications of a scaler would have no effect. 
        assert not dataset.scaled, 'Classifier.scale: The dataset has already been scaled.'

        embedding = dataset.embedding.cpu().numpy()
        if fit: # If specified, fit the scaler first. 
            self.scaler.fit(embedding)
        embedding = self.scaler.transform(embedding)
        dataset.embedding = torch.FloatTensor(embedding).to(DEVICE) 
        dataset.scaled = True

    def fit(self, datasets:tuple, epochs:int=10, lr:float=1e-8, batch_size:int=16, sampler=None, weight_loss:bool=False):

        assert datasets.test.scaled, 'Classifier.fit: The input test Dataset has not been scaled.' 
        assert datasets.train.scaled, 'Classifier.fit: The input train Dataset has not been scaled.'

        self.train() # Put the model in train mode.

        if weight_loss: 
            self.loss_func.fit(datasets.train) # Set the weights of the loss function.

        optimizer = torch.optim.Adam(self.parameters(), lr=lr)
        best_epoch, best_model_weights = 0, copy.deepcopy(self.state_dict())

        self.metrics['train_loss'] += [np.nan]
        self.metrics['test_acc'] += [self.accuracy(datasets.test)]

        if (sampler is None):
            dataloader = DataLoader(datasets.train, batch_size=batch_size, shuffle=True)
        else:
            dataloader = DataLoader(datasets.train, batch_sampler=sampler)

        pbar = tqdm(list(range(epochs))) 
        for epoch in pbar:
            self.loss = list()
            for batch in dataloader:
                # Evaluate the model on the batch in the training dataloader. 
                outputs, targets = self(batch['embedding']), batch['label_one_hot_encoded'] 
                loss = self.loss_func(outputs, targets)
                loss.backward() 
                self.loss.append(loss.item()) # Store each loss for computing metrics. 
                
                optimizer.step()
                optimizer.zero_grad()
            
            self.metrics['train_loss'] += [np.mean(self.loss)]
            self.metrics['test_acc'] += [self.accuracy(datasets.test)]

            pbar.set_description(f'Classifier.fit: test_acc={self.metrics['test_acc'][-1]}')
            pbar.refresh()

            if self.metrics['test_acc'][-1] > max(self.metrics['test_acc'][:-1]):
                best_epoch, best_model_weights = epoch, copy.deepcopy(self.state_dict())

        pbar.close()
        print(f'Classifier.fit: Loading best model weights from epoch {best_epoch}.')
        self.load_state_dict(best_model_weights) # Load the best model weights. 

        # Save training parameters in the model. 
        self.best_epoch = best_epoch
        self.epochs = epochs
        self.batch_size = batch_size
        self.lr = lr
        self.weight_loss = weight_loss
        self.feature_type = datasets.train.feature_type 
        self.sampler = sampler 


    @classmethod
    def load(cls, path:str):
        with open(path, 'rb') as f:
            # obj = pickle.load(f)
            obj = Unpickler(f).load()
        return obj.to(DEVICE)  

    def save(self, path:str):
        with open(path, 'wb') as f:
            pickle.dump(self, f)


    def plot(self, path:str=None):
        '''Visualize the training curve for the fitted model.'''
        assert self.fitted(), 'Classifier.plot: The model has not yet been fitted.'

        fig, ax = plt.subplots()
        handles = list()
        # Plot the training loss for each epoch. 
        handles += ax.plot(np.arange(self.epochs) + 1, self.metrics['train_loss'][1:], color='gray', label='train loss')
        ax.set_ylabel('cross-entropy loss')
        # Plot the accuracy on the validation set. 
        ax = ax.twinx()
        handles += ax.plot(np.arange(self.epochs + 1), self.metrics['test_acc'], color='black', label='test acc.')
        ax.set_ylabel('balanced accuracy')
        ax.set_xlabel('epoch')
        handles += [ax.vlines([self.best_epoch], ymin=0, ymax=ax.get_ylim()[-1], ls='--', color='tab:blue', lw=0.7, alpha=0.7, label='best epoch')]
        ax.legend(handles=handles)

        if path is not None:
            fig.savefig(path)
        
        plt.show()

