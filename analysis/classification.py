#!/usr/bin/env python
# -*-coding:utf-8 -*-
'''
@File    :   classification.py
@Time    :   2022/07/12 01:02:57
@Author  :   Kambiz Tavavi 
@Version :   0.1
@Contact :   ktavabi@gmail.com
@License :   MIT License (C)Copyright 2022, Kambiz Tavabi
@Desc    :   paros-bids LOGIT classification script
'''
# %%
import os.path as op

import janitor as jn  # noqa
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pandas_flavor as pf
import seaborn as sns
from mne import combine_evoked, io, read_source_estimate, spatial_src_adjacency
from mne.stats import spatio_temporal_cluster_test, summarize_clusters_stc
from pandas_profiling import ProfileReport
from scipy import stats as stats
from scipy.misc import derivative
from sklearn.datasets import load_iris
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

%config InlineBackend.figure_format = "retina"
%matplotlib widget

# %%
# PANDAS parameters
pd.options.display.html.table_schema = True
pd.options.display.max_rows = None
pd.set_option("display.max_rows", 500)
pd.set_option("display.max_columns", 100)
pd.set_option("display.width", 500)


@pf.register_dataframe_method
def str_remove(df, column_name: str, pattern: str = ""):
    """Wrapper to remove string patten from a column"""
    df[column_name] = df[column_name].str.replace(pattern, "")
    return df


@pf.register_dataframe_method
def explode(df: pd.DataFrame, column_name: str, sep: str):
    """Wrapper to expand column after text processing"""
    df["id"] = df.index
    wdf = pd.DataFrame(df[column_name].str.split(sep).fillna("").tolist()).stack().reset_index()
    # exploded_column = column_name
    wdf.columns = ["id", "depth", column_name]  # plural form to singular form
    # wdf[column_name] = wdf[column_name].apply(lambda x: x.strip())  # trim
    wdf.drop("depth", axis=1, inplace=True)

    return pd.merge(df, wdf, on="id", suffixes=("_drop", "")).drop(
        columns=["id", column_name + "_drop"]
    )


# %%
# Workspace parameters
seed = np.random.seed(42)
mrsi_data = "/Users/kam/Documents/Projects/Job/paros-bids/static/mrsiCsfCorrFits-20180417.xlsx"
# https://github.com/ydataai/pandas-profiling/issues/954
study_name = "paros-bids"
bids_root = "/Volumes/LaCie/paros-bids"
deriv_root = f"{bids_root}/derivatives/bids-pipeline"
subjects_dir = "/Volumes/LaCie/freesurfer"
payload_dir = "/Users/kam/Documents/Projects/Job/paros-bids/payload"

# %%
f = pd.ExcelFile(mrsi_data)
data = f.parse(sheet_name="FSLcorr_metab", header=1)
df = data.clean_names().str_remove("subject", pattern="sub-nbwr")
pivot_long_on = df.columns.values[1:]
df = df.pivot_longer(
    column_names=pivot_long_on, names_to="name", values_to="value", sort_by_appearance=True
)

df[["hemisphere", "mrsi"]] = df.name.apply(lambda x: pd.Series(str(x).split("_", 1)))

df.drop(labels=["name"], axis=1, inplace=True)
df = df.reorder_columns(["subject", "hemisphere", "mrsi", "value"]).encode_categorical(
    column_names=["hemisphere", "mrsi"]
)
df["grp"] = df["subject"].apply(lambda x: "asd" if np.int16(x) < 400 else "td")
df["hemisphere"] = df["hemisphere"].map({"left": "lh", "right": "rh"})
df = df[df.subject != "307"]
df = pd.pivot_table(
    df, values="value", index=["subject", "hemisphere", "grp"], columns=["mrsi"]
).reset_index()

# %%
print(df.head())

subjects = df["subject"].unique()
print(subjects)
meg_latency = np.zeros((len(subjects), 2, 2))  # subjects*conditions*hemisphere
meg_pos = np.zeros_like(meg_latency)

# Blow-up (subjects * conditions * hemisphere) into labeled TIDY data frame
_df = jn.expand_grid(others={"subject": subjects, "condition": [1, 2], "hemisphere": ["lh", "rh"]})

for si, subject in enumerate(subjects):
    for ci, condition in enumerate(["lexical", "nonlex"]):
        stc = read_source_estimate(
            op.join(
                deriv_root,
                f"sub-{subject}",
                "meg",
                f"sub-{subject}_task-lexicaldecision_{condition}+dSPM+morph2fsaverage+hemi-lh.stc",
            )
        )
        for hii, hem in enumerate(["lh", "rh"]):
            meg_pos[si, ci, hii], meg_latency[si, ci, hii] = stc.crop(tmin=0.150, tmax=0.500).get_peak(hemi=hem)

# %%
nn, cc, hh = meg_latency.shape
xs = nn * cc * hh
stc_data = np.hstack((meg_latency.reshape(xs, 1), meg_pos.reshape(xs, 1)))
stc_data = pd.DataFrame(stc_data, columns=["latency", "pos"])  # unlabeled meg features
df_meg = pd.concat([_df, stc_data], axis=1, ignore_index=True).clean_names()
df_meg = df_meg.rename_columns(
    new_column_names={"0": "subject", "1": "condition", "2": "hemisphere", "3": "latency", "4": "position"}
)
DATASET = pd.merge(df, df_meg, on=["subject", "hemisphere"], how="inner")
DATASET.describe


# %%
if not op.isfile (op.join(payload_dir, "profile.html")):
    profile = ProfileReport(
        DATASET,
        title="Pandas Profiling Report",
        vars={"num": {"low_categorical_threshold": 0}},
        explorative=True,
    )
    profile.to_file("profile.html")

sns.pairplot(DATASET, hue="grp")

# %%
# create a pipeline object
pipe = make_pipeline(StandardScaler(), LogisticRegression())
X = DATASET[
    [
        "crpluspcr",
        "gaba",
        "gabaovercr",
        "glu_80ms",
        "gluovergaba",
        "gpcpluspch",
        "mins",
        "naaplusnaag",
        "latency",
    ]
].values
X.shape
Y = DATASET["grp"].map({"asd": 1, "td": 2}).values
Y.shape
X_train, X_test, y_train, y_test = train_test_split(X, Y, random_state=seed)
model = pipe.fit(X_train, y_train)
model_accuracy = accuracy_score(pipe.predict(X_test), y_test)
model_accuracy

result = permutation_importance(model, X_test, y_test, n_repeats=10, random_state=seed)

# %%
fig, ax = plt.subplots()
feature_names = np.array(
    [
        "crpluspcr",
        "gaba",
        "gabaovercr",
        "glu_80ms",
        "gluovergaba",
        "gpcpluspch",
        "mins",
        "naaplusnaag",
        "latency",
    ]
)
sorted_idx = result.importances_mean.argsort()
ax.boxplot(result.importances[sorted_idx].T, vert=False, labels=feature_names[sorted_idx])
ax.set_title("Permutation Importance of each feature")
ax.set_ylabel("Features")
fig.tight_layout()
plt.show()

# The permutation feature importance is defined to be the decrease in a model score when a single feature value is randomly shuffled [1].

# [1] L. Breiman, “Random Forests”, Machine Learning, 45(1), 5-32, 2001.


# %%
f, ax = plt.subplots(nrows=1, ncols=1, figsize=(12, 10))
ax.set_title("Correlation Matrix", fontsize=10)
_filter_by = ["gaba", "glu_80ms", "gluovergaba", "latency"]
_grp_by = ["grp", "condition", "hemisphere"]
filteredData = DATASET[_grp_by + _filter_by]
sns.heatmap(filteredData.groupby(_grp_by).corr(method="pearson"), vmin=-1, vmax=1, cmap="coolwarm", annot=True)
for tick in ax.xaxis.get_major_ticks():
    tick.label.set_fontsize(10)
    tick.label.set_rotation(90)
for tick in ax.yaxis.get_major_ticks():
    tick.label.set_fontsize(10)
    tick.label.set_rotation(0)
plt.show()






# %%