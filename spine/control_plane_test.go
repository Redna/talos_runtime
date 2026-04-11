package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestHealthEndpoint(t *testing.T) {
	cfg := DefaultConfig()
	cp := NewControlPlane(cfg, nil, nil, nil)

	req := httptest.NewRequest("GET", "/health", nil)
	w := httptest.NewRecorder()
	cp.handleHealth(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("Expected 200, got %d", w.Code)
	}

	var result map[string]string
	json.NewDecoder(w.Body).Decode(&result)
	if result["status"] != "healthy" {
		t.Errorf("Expected healthy status, got %q", result["status"])
	}
}

func TestMessageEndpoint(t *testing.T) {
	cfg := DefaultConfig()
	stream := NewStreamManager(cfg)
	cp := NewControlPlane(cfg, nil, stream, nil)

	// Test missing method
	req := httptest.NewRequest("GET", "/message", nil)
	w := httptest.NewRecorder()
	cp.handleMessage(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("Expected 405 for GET, got %d", w.Code)
	}
}

func TestCommandEndpoint(t *testing.T) {
	cfg := DefaultConfig()
	cp := NewControlPlane(cfg, nil, nil, nil)

	// Test empty body (should return 400 from JSON decode error)
	req := httptest.NewRequest("POST", "/command", bytes.NewReader([]byte("")))
	w := httptest.NewRecorder()
	cp.handleCommand(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("Expected 400 for bad request, got %d", w.Code)
	}
}