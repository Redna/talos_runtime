package main

type SnapshotManager struct {
	snapshotsDir     string
	snapshotInterval int
}

func NewSnapshotManager(snapshotsDir string, snapshotInterval int) *SnapshotManager {
	return &SnapshotManager{
		snapshotsDir:     snapshotsDir,
		snapshotInterval: snapshotInterval,
	}
}