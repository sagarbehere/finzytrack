"""The engine's file-system port and its adapters (`port`, `darwin`; S0060). Only this package does file-system I/O
for writes; the writer, the journal and the path functions reach the disk through `port.FileSystem`."""
