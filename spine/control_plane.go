package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"strconv"
)

type ControlPlane struct {
	cfg        *Config
	supervisor *Supervisor
	stream     *StreamManager
	events     *EventLogger
	server     *http.Server
}

func NewControlPlane(cfg *Config, supervisor *Supervisor, streamManager *StreamManager, eventLogger *EventLogger) *ControlPlane {
	cp := &ControlPlane{
		cfg:        cfg,
		supervisor: supervisor,
		stream:     streamManager,
		events:     eventLogger,
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/status", cp.handleStatus)
	mux.HandleFunc("/events", cp.handleEvents)
	mux.HandleFunc("/state", cp.handleState)
	mux.HandleFunc("/message", cp.handleMessage)
	mux.HandleFunc("/command", cp.handleCommand)
	mux.HandleFunc("/health", cp.handleHealth)

	cp.server = &http.Server{
		Addr:    fmt.Sprintf(":%d", cfg.ControlPlanePort),
		Handler: mux,
	}
	return cp
}

func (cp *ControlPlane) Start() error {
	log.Printf("[Spine] Control plane listening on :%d", cp.cfg.ControlPlanePort)
	return cp.server.ListenAndServe()
}

func (cp *ControlPlane) Stop() {
	cp.server.Close()
}

func (cp *ControlPlane) handleStatus(w http.ResponseWriter, r *http.Request) {
	state := cp.stream.GetState(nil)
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(state)
}

func (cp *ControlPlane) handleEvents(w http.ResponseWriter, r *http.Request) {
	// Read last N events from the event log
	tail := 100
	if t := r.URL.Query().Get("tail"); t != "" {
		if n, err := strconv.Atoi(t); err == nil && n > 0 {
			tail = n
		}
	}
	// Return recent events - for now, return a placeholder
	// Full event querying will read from the JSONL files
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"tail": tail,
		"note": "Event querying from JSONL files",
	})
}

func (cp *ControlPlane) handleState(w http.ResponseWriter, r *http.Request) {
	state := cp.stream.GetState(nil)
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(state)
}

func (cp *ControlPlane) handleMessage(w http.ResponseWriter, r *http.Request) {
	if r.Method != "POST" {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var msg struct {
		Text string `json:"text"`
	}
	if err := json.NewDecoder(r.Body).Decode(&msg); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	cp.stream.QueueSystemNotice(msg.Text)
	w.WriteHeader(http.StatusOK)
}

func (cp *ControlPlane) handleCommand(w http.ResponseWriter, r *http.Request) {
	if r.Method != "POST" {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var cmd struct {
		Command string `json:"command"` // "pause", "resume", "force_fold", "force_restart"
	}
	if err := json.NewDecoder(r.Body).Decode(&cmd); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	switch cmd.Command {
	case "force_restart":
		cp.supervisor.RequestRestart("operator_command")
		w.WriteHeader(http.StatusOK)
	case "pause", "resume", "force_fold":
		cp.stream.QueueSystemNotice("[SYSTEM | Command: " + cmd.Command + "]")
		w.WriteHeader(http.StatusOK)
	default:
		http.Error(w, "unknown command", http.StatusBadRequest)
	}
}

func (cp *ControlPlane) handleHealth(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "healthy"})
}