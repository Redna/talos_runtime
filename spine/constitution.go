package main

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"sync"
)

type ConstitutionManager struct {
	path         string
	identityPath string
	mu           sync.RWMutex
	lastHash     string
	content      string
	identity     string
}

func NewConstitutionManager(constitutionPath, identityPath string) *ConstitutionManager {
	return &ConstitutionManager{
		path:         constitutionPath,
		identityPath: identityPath,
	}
}

// Load reads both files and returns the system prompt content.
// Returns an error if the constitution is empty or missing.
func (c *ConstitutionManager) Load() error {
	c.mu.Lock()
	defer c.mu.Unlock()

	constitution, err := os.ReadFile(c.path)
	if err != nil {
		return err
	}
	if len(constitution) == 0 {
		return ErrEmptyConstitution
	}

	identity, err := os.ReadFile(c.identityPath)
	if err != nil {
		return err
	}

	c.content = string(constitution)
	c.identity = string(identity)
	c.lastHash = hashContent(c.content + c.identity)
	return nil
}

// HasChanged returns true if the constitution or identity file has changed
// since the last load. Used to detect self-modification.
func (c *ConstitutionManager) HasChanged() bool {
	c.mu.RLock()
	defer c.mu.RUnlock()

	constitution, err := os.ReadFile(c.path)
	if err != nil {
		return true // file gone = change
	}
	identity, err := os.ReadFile(c.identityPath)
	if err != nil {
		return true
	}

	currentHash := hashContent(string(constitution) + string(identity))
	return currentHash != c.lastHash
}

// ReloadIfChanged reloads the files only if the hash has changed.
// Returns true if a reload happened, false if unchanged.
func (c *ConstitutionManager) ReloadIfChanged() (bool, error) {
	if !c.HasChanged() {
		return false, nil
	}
	return true, c.Load()
}

// SystemPrompt returns the combined system prompt (constitution + identity).
func (c *ConstitutionManager) SystemPrompt() string {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.content + "\n\n" + c.identity
}

var ErrEmptyConstitution = fmt.Errorf("constitution file is empty or missing — refusing to construct LLM call")

func hashContent(content string) string {
	h := sha256.Sum256([]byte(content))
	return hex.EncodeToString(h[:])
}
