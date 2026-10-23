# Dataset boundary

A dataset version consists of native training rows, their evidence, and a
manifest. File verification and publication must refer to the same version.
An unfinished execution is not a dataset version. Explicit partial exports
must agree in both run state and manifest.

`delivery.py` owns readiness rules. `trainer_formats.py` owns export schema
rules. `distribution.py` owns native-record counting rules. File I/O, ZIP,
hashing and worker jobs belong to infrastructure. No model is called here.
