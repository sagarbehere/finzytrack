"""The engine's file-system port and its adapters (`port`; `darwin` and `linux`, which share `posix`; S0060, S0125),
selected per OS by `this_os` (S0113). Only this package does file-system I/O for writes; the writer, the journal and
the path functions reach the disk through `port.FileSystem`."""
