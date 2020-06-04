#!/usr/bin/env python3
# @Time    : 31/5/20 10:59 AM
# @Author  : fangcheng.ji
# @FileName: loader.py

import bisect
import copy
import itertools
import logging
import numpy as np
import operator
import pickle
import torch.utils.data
from fvcore.common.file_io import PathManager
from tabulate import tabulate
from termcolor import colored

from detectron2.structures import BoxMode
from detectron2.utils.comm import get_world_size
from detectron2.utils.env import seed_all_rng
from detectron2.utils.logger import log_first_n

from detectron2.data.build import get_detection_dataset_dicts, trivial_batch_collator, worker_init_reset_seed
from detectron2.data import samplers
from detectron2.data.common import AspectRatioGroupedDataset, DatasetFromList, MapDataset
from detectron2.data.dataset_mapper import DatasetMapper

from .dataset_mapper import PlusDatasetMapper


def build_detection_train_loader(cfg, mapper=None):
    """
    A data loader is created by the following steps:

    1. Use the dataset names in config to query :class:`DatasetCatalog`, and obtain a list of dicts.
    2. Coordinate a random shuffle order shared among all processes (all GPUs)
    3. Each process spawn another few workers to process the dicts. Each worker will:
       * Map each metadata dict into another format to be consumed by the model.
       * Batch them by simply putting dicts into a list.

    The batched ``list[mapped_dict]`` is what this dataloader will yield.

    Args:
        cfg (CfgNode): the config
        mapper (callable): a callable which takes a sample (dict) from dataset and
            returns the format to be consumed by the model.
            By default it will be `DatasetMapper(cfg, True)`.

    Returns:
        an infinite iterator of training data
    """
    num_workers = get_world_size()
    images_per_batch = cfg.SOLVER.IMS_PER_BATCH
    assert (
        images_per_batch % num_workers == 0
    ), "SOLVER.IMS_PER_BATCH ({}) must be divisible by the number of workers ({}).".format(
        images_per_batch, num_workers
    )
    assert (
        images_per_batch >= num_workers
    ), "SOLVER.IMS_PER_BATCH ({}) must be larger than the number of workers ({}).".format(
        images_per_batch, num_workers
    )
    images_per_worker = images_per_batch // num_workers

    dataset_dicts = get_detection_dataset_dicts(
        cfg.DATASETS.TRAIN,
        filter_empty=cfg.DATALOADER.FILTER_EMPTY_ANNOTATIONS,
        min_keypoints=cfg.MODEL.ROI_KEYPOINT_HEAD.MIN_KEYPOINTS_PER_IMAGE
        if cfg.MODEL.KEYPOINT_ON
        else 0,
        proposal_files=cfg.DATASETS.PROPOSAL_FILES_TRAIN if cfg.MODEL.LOAD_PROPOSALS else None,
    )
    dataset = DatasetFromList(dataset_dicts, copy=False)

    if mapper is None:
        mapper = PlusDatasetMapper(cfg, dataset, True)
    dataset = MapDataset(dataset, mapper)

    sampler_name = cfg.DATALOADER.SAMPLER_TRAIN
    logger = logging.getLogger(__name__)
    logger.info("Using training sampler {}".format(sampler_name))
    if sampler_name == "TrainingSampler":
        sampler = samplers.TrainingSampler(len(dataset))
    elif sampler_name == "RepeatFactorTrainingSampler":
        sampler = samplers.RepeatFactorTrainingSampler(
            dataset_dicts, cfg.DATALOADER.REPEAT_THRESHOLD
        )
    else:
        raise ValueError("Unknown training sampler: {}".format(sampler_name))

    if cfg.DATALOADER.ASPECT_RATIO_GROUPING:
        data_loader = torch.utils.data.DataLoader(
            dataset,
            sampler=sampler,
            num_workers=cfg.DATALOADER.NUM_WORKERS,
            batch_sampler=None,
            collate_fn=operator.itemgetter(0),  # don't batch, but yield individual elements
            worker_init_fn=worker_init_reset_seed,
        )  # yield individual mapped dict
        data_loader = AspectRatioGroupedDataset(data_loader, images_per_worker)
    else:
        batch_sampler = torch.utils.data.sampler.BatchSampler(
            sampler, images_per_worker, drop_last=True
        )
        # drop_last so the batch always have the same size
        data_loader = torch.utils.data.DataLoader(
            dataset,
            num_workers=cfg.DATALOADER.NUM_WORKERS,
            batch_sampler=batch_sampler,
            collate_fn=trivial_batch_collator,
            worker_init_fn=worker_init_reset_seed,
        )

    return data_loader


if __name__=="__main__":
    config_file = "/data/fangcheng.ji/detectron2/projects/Alleria/configs/fcos_R_50_FPN_1x.yaml"

    from detectron2.config import get_cfg
    from detectron2.engine import default_setup
    from detectron2.utils.visualizer import Visualizer
    from detectron2.data import MetadataCatalog
    from datasets import register_all_datasets
    import os

    cfg = get_cfg()
    cfg.merge_from_file(config_file)
    cfg.freeze()
    default_setup(cfg, "")

    meta = MetadataCatalog.get(cfg.DATASETS.TRAIN)
    data_loader = build_detection_train_loader(cfg)

    dirname = "coco-data-vis"
    os.makedirs(dirname, exist_ok=True)
    data_iter = iter(data_loader)
    for idx in range(10):
        batches = next(data_iter)
        for image, data_dict in batches:
            visualizer = Visualizer(image, metadata=meta)
            vis = visualizer.draw_dataset_dict(data_dict)
            fpath = os.path.join(dirname, os.path.basename(data_dict["file_name"]))
            vis.save(fpath)


