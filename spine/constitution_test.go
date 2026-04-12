package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestConstitutionLoad(t *testing.T) {
	dir := t.TempDir()
	consPath := filepath.Join(dir, "CONSTITUTION.md")
	identPath := filepath.Join(dir, "identity.md")

	os.WriteFile(consPath, []byte("P0: Agency\nP1: Continuity"), 0644)
	os.WriteFile(identPath, []byte("I am Talos"), 0644)

	cm := NewConstitutionManager(consPath, identPath)
	if err := cm.Load(); err != nil {
		t.Fatalf("Load failed: %v", err)
	}

	prompt := cm.SystemPrompt()
	if prompt != "P0: Agency\nP1: Continuity\n\nI am Talos" {
		t.Errorf("Unexpected system prompt: %q", prompt)
	}
}

func TestEmptyConstitution(t *testing.T) {
	dir := t.TempDir()
	consPath := filepath.Join(dir, "CONSTITUTION.md")
	identPath := filepath.Join(dir, "identity.md")

	os.WriteFile(consPath, []byte(""), 0644)
	os.WriteFile(identPath, []byte("I am Talos"), 0644)

	cm := NewConstitutionManager(consPath, identPath)
	err := cm.Load()
	if err != ErrEmptyConstitution {
		t.Errorf("Expected ErrEmptyConstitution, got: %v", err)
	}
}

func TestConstitutionChangeDetection(t *testing.T) {
	dir := t.TempDir()
	consPath := filepath.Join(dir, "CONSTITUTION.md")
	identPath := filepath.Join(dir, "identity.md")

	os.WriteFile(consPath, []byte("P0: Agency"), 0644)
	os.WriteFile(identPath, []byte("I am Talos"), 0644)

	cm := NewConstitutionManager(consPath, identPath)
	cm.Load()

	if cm.HasChanged() {
		t.Error("Should not detect change right after load")
	}

	os.WriteFile(consPath, []byte("P0: Agency\nP1: Continuity"), 0644)

	if !cm.HasChanged() {
		t.Error("Should detect change after modification")
	}

	changed, err := cm.ReloadIfChanged()
	if !changed || err != nil {
		t.Errorf("ReloadIfChanged should return true, nil; got %v, %v", changed, err)
	}
}
