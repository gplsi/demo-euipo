---
library_name: transformers
license: apache-2.0
base_model: gplsi/Aitana-2B-S-Instruct
tags:
- generated_from_trainer
datasets:
- /home/gplsi/GPLSI/ALIA/datos/euipo_axolotl/euipo_train_chat.jsonl
model-index:
- name: home/gplsi/GPLSI/ALIA/modelos/Instruction/EUIPO_Aitana_2B_S_Instruct_absolute_refusals_v1
  results: []
---

<!-- This model card has been generated automatically according to the information the Trainer had access to. You
should probably proofread and complete it, then remove this comment. -->

[<img src="https://raw.githubusercontent.com/axolotl-ai-cloud/axolotl/main/image/axolotl-badge-web.png" alt="Built with Axolotl" width="200" height="32"/>](https://github.com/axolotl-ai-cloud/axolotl)
<details><summary>See axolotl config</summary>

axolotl version: `0.16.1`
```yaml
base_model: gplsi/Aitana-2B-S-Instruct
model_type: AutoModelForCausalLM
tokenizer_type: AutoTokenizer
#processor_type: AutoProcessor
is_falcon_derived_model:
is_llama_derived_model:
is_qwen_derived_model:
is_mistral_derived_model:

load_in_8bit: false
load_in_4bit: false
strict: false
gptq: false

device_map: null
eot_tokens:
  - "<|im_end|>"

datasets:
  - path: /home/gplsi/GPLSI/ALIA/datos/euipo_axolotl/euipo_train_chat.jsonl
    ds_type: json
    split: train
    type: chat_template
    field_messages: messages
    roles_to_train: ["assistant"]   # la pérdida solo se calcula sobre la respuesta
    train_on_eos: turn

val_set_size: 0.005
dataloader_num_workers: 4
dataloader_pin_memory: true

chat_template: tokenizer_default
dataset_prepared_path: /home/gplsi/GPLSI/ALIA/datos/euipo_axolotl/dataset_cache/
dataset_num_proc: 32
shuffle_merged_datasets: true

output_dir: /home/gplsi/GPLSI/ALIA/modelos/Instruction/EUIPO_Aitana_2B_S_Instruct_absolute_refusals_v1

adapter:
lora_model_dir:

sequence_len: 3072
sample_packing: false
eval_sample_packing: false
pad_to_sequence_len: false

#tokens:
#  - "<tool_call>"
#  - "<tool_response>"
#  - "<tools>"
#  - "</tool_call>"
#  - "</tool_response>"
#  - "</tools>"
#  - "<reserved1>"
#  - "<reserved2>"

#special_tokens:
#   pad_token: <|end_of_text|>

#neftune_noise_alpha: 5

wandb_project: AITANA_INSTRUCT_SFT
wandb_entity: gplsi_continual
wandb_watch:
wandb_name: exp_30_Instruct_EUIPO_absolute_refusals_Aitana_2B_S_Instruct
wandb_log_model:
wandb_mode: "offline"

gradient_accumulation_steps: 2
micro_batch_size: 32
eval_batch_size: 1
num_epochs: 2
optimizer: adamw_torch_fused
adam_beta1: 0.9
adam_beta2: 0.99
adam_epsilon: 1e-8
lr_scheduler: cosine
learning_rate: 0.00002
cosine_min_lr_ratio: 0.01

group_by_length: false
bf16: true
fp16: false
tf32: true

gradient_checkpointing: false
early_stopping_patience:
resume_from_checkpoint:
auto_resume_from_checkpoints: false
local_rank:
logging_steps: 1
flash_attention: true
warmup_ratio: 0.03
evals_per_epoch: 2
do_causal_lm_eval: false
eval_causal_lm_metrics: []
saves_per_epoch: 2
save_total_limit: 6
debug:
weight_decay: 0
#deepspeed: deepspeed_configs/zero2.json
#ddp: true
fsdp:
fsdp_config:
 offload_params: false
 cpu_ram_efficient_loading: false
 auto_wrap_policy: TRANSFORMER_BASED_WRAP
 transformer_layer_cls_to_wrap: LlamaDecoderLayer
 state_dict_type: FULL_STATE_DICT
 reshard_after_forward: false
 activation_checkpointing: true
fsdp_version: 2
#ddp_find_unused_parameters: true
# HSDP via N-D Parallelism
dp_shard_size: 1      # GPUs por nodo (FSDP dimension)
dp_replicate_size: 1  # número de nodos (DDP dimension)
tensor_parallel_size: 1
context_parallel_size: 1

seed: 42

plugins:
  - axolotl.integrations.liger.LigerPlugin
liger_rope: true
liger_rms_norm: true
liger_glu_activation: true
liger_layer_norm: true
liger_fused_linear_cross_entropy: true

```

</details><br>

# home/gplsi/GPLSI/ALIA/modelos/Instruction/EUIPO_Aitana_2B_S_Instruct_absolute_refusals_v1

This model is a fine-tuned version of [gplsi/Aitana-2B-S-Instruct](https://huggingface.co/gplsi/Aitana-2B-S-Instruct) on the /home/gplsi/GPLSI/ALIA/datos/euipo_axolotl/euipo_train_chat.jsonl dataset.
It achieves the following results on the evaluation set:
- Loss: 0.4746
- Ppl: 1.6074
- Memory/max Active (gib): 21.55
- Memory/max Allocated (gib): 21.55
- Memory/device Reserved (gib): 44.89

## Model description

More information needed

## Intended uses & limitations

More information needed

## Training and evaluation data

More information needed

## Training procedure

### Training hyperparameters

The following hyperparameters were used during training:
- learning_rate: 2e-05
- train_batch_size: 32
- eval_batch_size: 1
- seed: 42
- distributed_type: multi-GPU
- gradient_accumulation_steps: 2
- total_train_batch_size: 64
- optimizer: Use OptimizerNames.ADAMW_TORCH_FUSED with betas=(0.9,0.99) and epsilon=1e-08 and optimizer_args=No additional optimizer arguments
- lr_scheduler_type: cosine
- lr_scheduler_warmup_steps: 8
- training_steps: 271

### Training results

| Training Loss | Epoch  | Step | Validation Loss | Ppl    | Active (gib) | Allocated (gib) | Reserved (gib) |
|:-------------:|:------:|:----:|:---------------:|:------:|:------------:|:---------------:|:--------------:|
| No log        | 0      | 0    | 1.9210          | 6.8277 | 13.15        | 13.15           | 13.17          |
| 0.4980        | 0.5018 | 68   | 0.5289          | 1.6971 | 21.55        | 21.55           | 46.32          |
| 0.4551        | 1.0    | 136  | 0.4912          | 1.6343 | 21.55        | 21.55           | 46.36          |
| 0.3535        | 1.5018 | 204  | 0.4756          | 1.6090 | 21.55        | 21.55           | 48.16          |
| 0.4053        | 1.9963 | 271  | 0.4746          | 1.6074 | 21.55        | 21.55           | 44.89          |


### Framework versions

- Transformers 5.14.1
- Pytorch 2.11.0+cu130
- Datasets 4.5.0
- Tokenizers 0.22.2
