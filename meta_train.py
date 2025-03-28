
import random
import math
import os
import logging
from collections import Counter

import numpy as np
import torch

from transformers import GPT2LMHeadModel, AutoConfig

from training import *
from dataloading import *
from dataset_iterators import *
from models import *
from evaluations import *

import argparse

if torch.cuda.is_available():
    device = torch.device('cuda')
else:
    device = torch.device('cpu')


parser = argparse.ArgumentParser()

# Corpus arguments
parser.add_argument("--n_meta_train", help="Episodes in the meta-training set. Just one epoch will be run.", type=int, default=20000)
parser.add_argument("--n_meta_valid", help="Episodes in the meta-validation set", type=int, default=100)
parser.add_argument("--n_meta_test", help="Episodes in the meta-test set", type=int, default=100)

parser.add_argument("--dataset", help="Data to meta-train on. Options: dnf", type=str, default="dnf")
parser.add_argument("--min_n_features", help="Minimum number of features in the dataset", type=int, default=4)
parser.add_argument("--max_n_features", help="Maximum number of features in the dataset", type=int, default=4)
parser.add_argument("--min_n_train", help="Minimum number of training examples in the dataset", type=int, default=10)
parser.add_argument("--max_n_train", help="Maximum number of training examples in the dataset", type=int, default=10)
parser.add_argument("--train_batch_size", help="Batch size for each episode's training set. If omitted, the entire training set is a single batch of varying size", type=int, default=None)
parser.add_argument("--b", help="Outlier parameter", type=float, default=1)
parser.add_argument("--no_true_false_top", help="True: 30% T or F, False: 90% T or F", type=bool, default=True)
parser.add_argument("--reject_sampling", help="Reject sample if all true/all false", type=bool, default=True)

# Architecture arguments
parser.add_argument("--model", help="Model MLP or Transformer or LSTM", type=str, default="MLP")
parser.add_argument("--n_hidden", help="Hidden layer units", type=int, default=128)
parser.add_argument("--n_layer", help="Number of layers", type=int, default=4)
parser.add_argument("--dropout", help="Dropout", type=float, default=0.1)

# Training arguments
parser.add_argument("--n_epochs", help="Number of training epochs", type=int, default=1)
parser.add_argument("--eval_every", help="Number of training steps to go between evaluations", type=int, default=100)
parser.add_argument("--weight_decay", help="Weight decay", type=float, default=1e-1)
parser.add_argument("--learning_rate", help="Outer-loop learning rate", type=float, default=5e-4)
parser.add_argument("--inner_lr", help="Inner-loop learning rate", type=float, default=1e-1)
parser.add_argument("--lr_scheduler_type", help="Learning rate scheduler type (cosine or constant)", type=str, default="cosine")
parser.add_argument("--warmup_proportion", help="Proportion of total steps that are warmup", type=float, default=0.05)
parser.add_argument("--patience", help="Patience", type=int, default=None)
parser.add_argument("--lr_decay_patience", help="Learning rate decay paatience", type=int, default=None)
parser.add_argument("--multi_step_loss", help="use multi-step loss", action='store_true')
parser.add_argument("--pseudo", help="do pre-training (pseudo meta-training) instead of true meta-training", action='store_true')
parser.add_argument("--epochs_per_episode", help="Number of epochs to use for each training episode", type=int, default=1)
parser.add_argument("--overfit", help="Use same meta-train and meta-val ", type=bool, default=False)
parser.add_argument("--one_example", help="Train on one example, test on one example", type=bool, default=False)
parser.add_argument("--ignore_first", help="Ignore first row", type=bool, default=False)
# Saving arguments
parser.add_argument("--model_name", help="Model name prefix", type=str, default=None)
parser.add_argument("--weight_dir", help="Directory to save model weights in", type=str, default="weights/")
parser.add_argument("--log_dir", help="Directory to save logs in", type=str, default="logs/")

# Evaluation arguments
parser.add_argument("--eval", help="Just evaluate, don't train", action='store_true')
parser.add_argument("--eval_valid", help="evaluate on the validation set", action='store_true')
parser.add_argument("--eval_table3", help="evaluate on table 3", action='store_true')
parser.add_argument("--table3_n_runs", help="number of runs to average over for the Table 3 evaluation", type=int, default=100)
parser.add_argument("--eval_table4", help="evaluate on table 4", action='store_true')
parser.add_argument("--table4_n_runs", help="number of runs to average over for the Table 4 evaluation", type=int, default=100)
parser.add_argument("--eval_table5", help="evaluate on table 5", action='store_true')
parser.add_argument("--table5_n_runs", help="number of runs to average over for the Table 5 evaluation", type=int, default=100)
parser.add_argument("--eval_table6", help="evaluate on table 6", action='store_true')
parser.add_argument("--table6_n_runs", help="number of runs to average over for the Table 6 evaluation", type=int, default=100)
parser.add_argument("--eval_wudsy", help="evaluate on Piantadosi examples", action='store_true')
parser.add_argument("--wudsy_n_runs", help="number of runs to average over for the wudsy evaluation", type=int, default=100)
parser.add_argument("--random_n_runs", help="number of runs to average over for the wudsy evaluation", type=int, default=100)
args = parser.parse_args()



################################################################################################
# Set up logging
################################################################################################

if not args.weight_dir.endswith("/"):
    args.weight_dir = args.weight_dir + "/"
if not args.log_dir.endswith("/"):
    args.log_dir = args.log_dir + "/"

if args.eval_table3:
    log_file_name = args.model_name + "_eval_table3"
elif args.eval_table4:
    log_file_name = args.model_name + "_eval_table4"
elif args.eval_table5:
    log_file_name = args.model_name + "_eval_table5"
elif args.eval_table6:
    log_file_name = args.model_name + "_eval_table6"
elif args.eval_wudsy:
    log_file_name = args.model_name + "_eval_wudsy"
else:
    model_name = args.model_name
    model_index = 0
    args.model_name = model_name + "_" + str(model_index)
    while args.model_name + ".log" in os.listdir(args.log_dir):
        model_index += 1
        args.model_name = model_name + "_" + str(model_index)

    log_file_name = args.model_name

    random.seed(model_index)
    np.random.seed(model_index)
    torch.manual_seed(model_index)

logging.basicConfig(format='%(asctime)s %(levelname)-8s %(message)s', level=logging.INFO, handlers=[logging.StreamHandler(),logging.FileHandler(args.log_dir + log_file_name + ".log")])


logging.info(args)


################################################################################################
# Set up the data
################################################################################################

if args.dataset == "dnf":
    create_dataset = dnf_dataset(min_n_features=args.min_n_features, max_n_features=args.max_n_features,
                                 min_n_train=args.min_n_train, max_n_train=args.max_n_train,
                                 train_batch_size=args.train_batch_size, no_true_false_top = args.no_true_false_top,
                                 b=args.b, reject_sampling=args.reject_sampling)
if args.dataset == "random":
        create_dataset = random_dataset(min_n_features=args.min_n_features, max_n_features=args.max_n_features,
                                 min_n_train=args.min_n_train, max_n_train=args.max_n_train,
                                 train_batch_size=args.train_batch_size, no_true_false_top = args.no_true_false_top,
                                 b=args.b, reject_sampling=args.reject_sampling)
if args.dataset == "wudsy_single_feature":
         create_dataset = wudsy_single_feature_dataset(min_n_features=args.min_n_features, max_n_features=args.max_n_features,
                                 min_n_train=args.min_n_train, max_n_train=args.max_n_train,
                                 train_batch_size=args.train_batch_size, no_true_false_top = args.no_true_false_top,
                                 b=args.b, reject_sampling=args.reject_sampling)

if args.dataset == "wudsy_single_feature_45":
         create_dataset = wudsy_single_feature_45_dataset(min_n_features=args.min_n_features, max_n_features=args.max_n_features,
                                 min_n_train=args.min_n_train, max_n_train=args.max_n_train,
                                 train_batch_size=args.train_batch_size, no_true_false_top = args.no_true_false_top,
                                 b=args.b, reject_sampling=args.reject_sampling)
if args.dataset == "wudsy_single_feature_45_train_test":
         create_dataset = wudsy_single_feature_45_dataset_train_test(min_n_features=args.min_n_features, max_n_features=args.max_n_features,
                                 min_n_train=args.min_n_train, max_n_train=args.max_n_train,
                                 train_batch_size=args.train_batch_size, no_true_false_top = args.no_true_false_top,
                                 b=args.b, reject_sampling=args.reject_sampling)

if args.dataset == "wudsy_single_feature_45_mult_obj_row":
         create_dataset = wudsy_single_feature_45_dataset_mult_obj_row(min_n_features=args.min_n_features, max_n_features=args.max_n_features,
                                 min_n_train=args.min_n_train, max_n_train=args.max_n_train,
                                 train_batch_size=args.train_batch_size, no_true_false_top = args.no_true_false_top,
                                 b=args.b, reject_sampling=args.reject_sampling)

if args.dataset == "FlatBoolean":
         create_dataset = FlatBoolean(min_n_features=args.min_n_features, max_n_features=args.max_n_features,
                                 min_n_train=args.min_n_train, max_n_train=args.max_n_train,
                                 train_batch_size=args.train_batch_size, no_true_false_top = args.no_true_false_top,
                                 b=args.b, reject_sampling=args.reject_sampling)

if args.dataset == "wudsy":
         create_dataset = wudsy_dataset(min_n_features=args.min_n_features, max_n_features=args.max_n_features,
                                 min_n_train=args.min_n_train, max_n_train=args.max_n_train,
                                 train_batch_size=args.train_batch_size, no_true_false_top = args.no_true_false_top,
                                 b=args.b, reject_sampling=args.reject_sampling)
if args.dataset == "all_rules":
         create_dataset = all_rules_dataset(min_n_features=args.min_n_features, max_n_features=args.max_n_features,
                                 min_n_train=args.min_n_train, max_n_train=args.max_n_train,
                                 train_batch_size=args.train_batch_size, no_true_false_top = args.no_true_false_top,
                                 b=args.b, reject_sampling=args.reject_sampling)
if args.dataset == "fol":
         create_dataset = fol_dataset(min_n_features=args.min_n_features, max_n_features=args.max_n_features,
                                 min_n_train=args.min_n_train, max_n_train=args.max_n_train,
                                 train_batch_size=args.train_batch_size, no_true_false_top = args.no_true_false_top,
                                 b=args.b, reject_sampling=args.reject_sampling)

meta_dataset = MetaLogicDataset(create_dataset=create_dataset, meta_train_size=args.n_meta_train, meta_valid_size=args.n_meta_valid, meta_test_size=args.n_meta_test, overfit=args.overfit)



################################################################################################
# Set up the model
################################################################################################
if args.model == 'MLP_RR':
    model = MLPClassifierRationalRules(n_features=args.max_n_features, hidden_size=args.n_hidden, n_layers=args.n_layer, dropout=args.dropout, nonlinearity="ReLU", model_name=args.model_name, save_dir=args.weight_dir)
if args.model == 'MLP':
    model = MLPClassifier(n_features=args.max_n_features, hidden_size=args.n_hidden, n_layers=args.n_layer, dropout=args.dropout, nonlinearity="ReLU", model_name=args.model_name, save_dir=args.weight_dir)
elif args.model == 'Transformer':
    model = Transformer(n_features=args.max_n_features, hidden_size=args.n_hidden, n_layers=args.n_layer, dropout=args.dropout, nonlinearity="ReLU", model_name=args.model_name, save_dir=args.weight_dir)
elif args.model == 'LSTM':
    model = LSTM(n_features=args.max_n_features, hidden_size=args.n_hidden, n_layers=args.n_layer, dropout=args.dropout, nonlinearity="ReLU", model_name=args.model_name, save_dir=args.weight_dir)

model_size = sum(t.numel() for t in model.parameters())
logging.info(f"Model size: {model_size/1000**2:.1f}M parameters")



################################################################################################
# Meta-train
################################################################################################

warmup_steps = math.ceil(args.warmup_proportion*args.n_epochs*len(meta_dataset.train))

if args.train_batch_size is None:
    vary_train_batch_size = True
else:
    vary_train_batch_size = False

if args.pseudo:
    trainer = PseudoMetaTrainer(
            model=model,
            train_datasplit=meta_dataset.train,
            eval_datasplit=meta_dataset.valid,
            n_epochs=args.n_epochs,
            patience=args.patience,
            lr_decay_patience=args.lr_decay_patience,
            weight_decay=args.weight_decay,   
            learning_rate=args.learning_rate,
            lr_scheduler_type=args.lr_scheduler_type,
            warmup_steps=warmup_steps,
            eval_every=args.eval_every,
            log_every=args.eval_every,
            inner_lr=args.inner_lr,
            multi_step_loss=args.multi_step_loss,
            vary_train_batch_size=vary_train_batch_size,
            epochs_per_episode=args.epochs_per_episode,
            )
elif args.one_example:
    trainer = MetaTrainerOneExample(
            model=model,
            train_datasplit=meta_dataset.train,
            eval_datasplit=meta_dataset.valid,
            n_epochs=args.n_epochs,
            patience=args.patience,
            lr_decay_patience=args.lr_decay_patience,
            weight_decay=args.weight_decay,   
            learning_rate=args.learning_rate,
            lr_scheduler_type=args.lr_scheduler_type,
            warmup_steps=warmup_steps,
            eval_every=args.eval_every,
            log_every=args.eval_every,
            inner_lr=args.inner_lr,
            multi_step_loss=args.multi_step_loss,
            vary_train_batch_size=vary_train_batch_size,
            epochs_per_episode=args.epochs_per_episode,
            ignore_first=args.ignore_first
            )
else:
    trainer = MetaTrainer(
            model=model,
            train_datasplit=meta_dataset.train,
            eval_datasplit=meta_dataset.valid,
            n_epochs=args.n_epochs,
            patience=args.patience,
            lr_decay_patience=args.lr_decay_patience,
            weight_decay=args.weight_decay,   
            learning_rate=args.learning_rate,
            lr_scheduler_type=args.lr_scheduler_type,
            warmup_steps=warmup_steps,
            eval_every=args.eval_every,
            log_every=args.eval_every,
            inner_lr=args.inner_lr,
            multi_step_loss=args.multi_step_loss,
            vary_train_batch_size=vary_train_batch_size,
            epochs_per_episode=args.epochs_per_episode,
            )

if not args.eval: 
    trainer.train()

if not args.model_name.startswith("random"):
    trainer.model.load()



################################################################################################
# Evaluate
################################################################################################

if args.eval_table3:
    table3_n_runs(trainer.model, lr=args.inner_lr, train_batch_size=args.train_batch_size, vary_train_batch_size=vary_train_batch_size, epochs=args.epochs_per_episode, n_runs=args.table3_n_runs, max_n_features=args.max_n_features)

if args.eval_table4:
    table4_n_runs(trainer.model, lr=args.inner_lr, train_batch_size=args.train_batch_size, vary_train_batch_size=vary_train_batch_size, epochs=args.epochs_per_episode, n_runs=args.table4_n_runs, max_n_features=args.max_n_features)

if args.eval_table5:
    table5_n_runs(trainer.model, lr=args.inner_lr, train_batch_size=args.train_batch_size, vary_train_batch_size=vary_train_batch_size, epochs=args.epochs_per_episode, n_runs=args.table5_n_runs, max_n_features=args.max_n_features)

if args.eval_table6:
    table6_n_runs(trainer.model, lr=args.inner_lr, train_batch_size=args.train_batch_size, vary_train_batch_size=vary_train_batch_size, epochs=args.epochs_per_episode, n_runs=args.table6_n_runs, max_n_features=args.max_n_features)

if args.eval_wudsy:
    wudsy_n_runs(trainer.model, lr=args.inner_lr, train_batch_size=args.train_batch_size, vary_train_batch_size=vary_train_batch_size, epochs=args.epochs_per_episode, n_runs=args.table6_n_runs, max_n_features=args.max_n_features)

if (not args.eval) or args.eval_valid:
    trainer.evaluate(eval_datasplit=meta_dataset.valid, name="Validation")





