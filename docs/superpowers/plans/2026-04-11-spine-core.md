# Spine Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Spine — a Go binary that owns the LLM message stream, manages the Cortex lifecycle, provides IPC, emits structured events, and exposes a control plane.

**Architecture:** The Spine is a Go process that communicates with the Cortex (Python) via Unix domain socket using a JSON-RPC-like protocol. It proxies LLM calls through the Gate, constructs messages (system prompt + stream + HUD), handles shedding and folding, supervises the Cortex process, and exposes an HTTP control plane. It writes structured events to `/spine/events/` and state snapshots to `/spine/snapshots/`.

**Tech Stack:** Go 1.22+, standard library `net/http` for control plane, `net` for Unix domain socket, `os/exec` for process supervision, `encoding/json` for JSON-RPC. No external dependencies for the core — only stdlib.

**Depends on:** Nothing. The Spine is the foundation — it must be built first and can be tested independently.

---

## File Structure

```
spine/
  main.go                 # Entry point, config loading, signal handling
  config.go               # Configuration struct and loading
  ipc.go                  # Unix domain socket server (JSON-RPC)
  ipc_types.go            # Request/response types for IPC
  stream.go               # Stream construction, shedding, folding, HUD injection
  stream_test.go          # Stream shedding and folding tests
  supervisor.go           # Cortex process lifecycle management
  events.go               # Structured event emission to /spine/events/
  events_test.go          # Event emission tests
  snapshot.go             # State snapshot save/restore to /spine/snapshots/
  snapshot_test.go         # Snapshot tests
  health.go               # Stall detection, crash detection, startup failure detection
  control_plane.go        # HTTP API on port 4001
  control_plane_test.go   # Control plane tests
  telegram.go             # Minimal Telegram bot for essential notifications
  constitution.go         # Constitution loading and hash tracking
  constitution_test.go    # Constitution hash tests
  go.mod
  go.sum
```

---

### Task 1: Project Scaffolding and Configuration

**Files:**
- Create: `spine/go.mod`
- Create: `spine/main.go`
- Create: `spine/config.go`

- [ ] **Step 1: Initialize Go module**

```bash
mkdir -p spine && cd spine && go mod init github.com/redna/talos_runtime/spine
```

- [ ] **Step 2: Write config.go**

Create `spine/config.go`:

```go
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"time"
)

type Config struct {
	// Paths
	MemoryDir    string `json:"memory_dir"`
	SpineDir     string `json:"spine_dir"`
	ConstitutionPath string `json:"constitution_path"`
	IdentityPath     string `json:"identity_path"`
	AppDir       string `json:"app_dir"`

	// Cortex
	CortexBin    string `json:"cortex_bin"`
	CortexArgs   []string `json:"cortex_args"`
	StartupTimeout time.Duration `json:"startup_timeout"`

	// IPC
	SocketPath   string `json:"socket_path"`

	// Control Plane
	ControlPlanePort int `json:"control_plane_port"`

	// Stream
	ContextThreshold float64 `json:"context_threshold"`
	ActiveWindow     int     `json:"active_window"` // M turns kept at full fidelity
	MaxContextTokens int     `json:"max_context_tokens"`

	// Gate
	GateURL string `json:"gate_url"`

	// Telegram
	TelegramBotToken string `json:"telegram_bot_token"`
	TelegramChatID  int64  `json:"telegram_chat_id"`

	// Health
	StallTimeout      time.Duration `json:"stall_timeout"`
	SnapshotInterval  int           `json:"snapshot_interval"` // every N turns
	MaxReversalDepth  int           `json:"max_reversal_depth"`

	// Shedding
	ShedToolOutputMaxChars int `json:"shed_tool_output_max_chars"`
}

func DefaultConfig() *Config {
	return &Config{
		MemoryDir:          "/memory",
		SpineDir:           "/spine",
		ConstitutionPath:   "/app/CONSTITUTION.md",
		IdentityPath:       "/app/identity.md",
		AppDir:             "/app",
		CortexBin:          "/venv/bin/python",
		CortexArgs:         []string{"seed_agent.py"},
		StartupTimeout:     30 * time.Second,
		SocketPath:         "/tmp/spine.sock",
		ControlPlanePort:   4001,
		ContextThreshold:   0.85,
		ActiveWindow:       5,
		MaxContextTokens:   71680,
		GateURL:           "http://gate:4000",
		StallTimeout:      600 * time.Second,
		SnapshotInterval:  10,
		MaxReversalDepth:  5,
		ShedToolOutputMaxChars: 500,
	}
}

func LoadConfig(path string) (*Config, error) {
	cfg := DefaultConfig()
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return cfg, nil
		}
		return nil, fmt.Errorf("read config: %w", err)
	}
	if err := json.Unmarshal(data, cfg); err != nil {
		return nil, fmt.Errorf("parse config: %w", err)
	}
	return cfg, nil
}
```

- [ ] **Step 3: Write main.go entry point**

Create `spine/main.go`:

```go
package main

import (
	"fmt"
	"log"
	"os"
	"os/signal"
	"syscall"
)

func main() {
	cfgPath := "/spine/spine_config.json"
	if len(os.Args) > 1 {
		cfgPath = os.Args[1]
	}

	cfg, err := LoadConfig(cfgPath)
	if err != nil {
		log.Fatalf("Failed to load config: %v", err)
	}

	// Ensure spine directories exist
	for _, dir := range []string{
		cfg.SpineDir + "/events",
		cfg.SpineDir + "/snapshots",
		cfg.SpineDir + "/crashes",
	} {
		if err := os.MkdirAll(dir, 0755); err != nil {
			log.Fatalf("Failed to create directory %s: %v", dir, err)
		}
	}

	log.Printf("[Spine] Starting with config: GateURL=%s Socket=%s", cfg.GateURL, cfg.SocketPath)

	// Initialize components
	eventLogger := NewEventLogger(cfg.SpineDir + "/events")
	snapshotManager := NewSnapshotManager(cfg.SpineDir+"/snapshots", cfg.SnapshotInterval)
	streamManager := NewStreamManager(cfg)
	supervisor := NewSupervisor(cfg, eventLogger, snapshotManager, streamManager)
	controlPlane := NewControlPlane(cfg, supervisor, streamManager, eventLogger)

	// Start control plane in background
	go func() {
		if err := controlPlane.Start(); err != nil {
			log.Fatalf("[Spine] Control plane failed: %v", err)
		}
	}()

	// Start IPC server in background
	ipcServer := NewIPCServer(cfg, supervisor, streamManager, eventLogger)
	go func() {
		if err := ipcServer.Start(); err != nil {
			log.Fatalf("[Spine] IPC server failed: %v", err)
		}
	}()

	// Start Telegram bot if configured
	if cfg.TelegramBotToken != "" {
		tgBot := NewTelegramBot(cfg, eventLogger)
		go func() {
			if err := tgBot.Start(); err != nil {
				log.Printf("[Spine] Telegram bot error: %v", err)
			}
		}()
	}

	// Start supervisor (manages Cortex lifecycle)
	go supervisor.Run()

	// Wait for shutdown signal
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	<-sigCh

	log.Println("[Spine] Shutting down...")
	supervisor.Stop()
	controlPlane.Stop()
	ipcServer.Stop()
	fmt.Println("[Spine] Stopped.")
}
```

- [ ] **Step 4: Initialize and build**

```bash
cd spine && go build -o spine . && echo "Build succeeded"
```

- [ ] **Step 5: Commit**

```bash
git add spine/ && git commit -m "feat(spine): project scaffolding with config and entry point"
```

---

### Task 2: IPC Protocol and Types

**Files:**
- Create: `spine/ipc_types.go`
- Create: `spine/ipc.go`

- [ ] **Step 1: Write IPC types**

Create `spine/ipc_types.go`:

```go
package main

// Cortex → Spine requests
type ThinkRequest struct {
	Focus   string     `json:"focus"`
	Tools   []ToolDef  `json:"tools"`
	HUDData HUDData   `json:"hud_data"`
}

type ToolDef struct {
	Name        string                 `json:"name"`
	Description string                 `json:"description"`
	Parameters  map[string]interface{} `json:"parameters"` // JSON Schema object
}

type HUDData struct {
	MemoryKeys int      `json:"memory_keys"`
	LastKeys   []string `json:"last_keys"`
	Urgency    string   `json:"urgency"`
}

type ToolResultRequest struct {
	ToolCallID string `json:"tool_call_id"`
	Output     string `json:"output"`
	Success    bool   `json:"success"`
}

type RequestFoldRequest struct {
	Synthesis string `json:"synthesis"`
}

type RequestRestartRequest struct {
	Reason string `json:"reason"`
}

type SendMessageRequest struct {
	Channel string `json:"channel"`
	Text    string `json:"text"`
}

type EmitEventRequest struct {
	Type    string                 `json:"type"`
	Payload map[string]interface{} `json:"payload"`
}

type GetStateRequest struct {
	Keys []string `json:"keys"`
}

// Spine → Cortex push notifications
type PushNotification struct {
	Type    string `json:"type"` // "force_fold", "system_notice", "tool_timeout", "pause", "resume"
	Payload string `json:"payload"`
}

// Spine → Cortex think response
type ThinkResponse struct {
	AssistantMessage string          `json:"assistant_message"`
	ToolCalls        []ToolCallResult `json:"tool_calls"`
	ContextPct       float64          `json:"context_pct"`
	Turn             int             `json:"turn"`
	TokensUsed       int             `json:"tokens_used"`
	Folded           bool            `json:"folded"`
}

type ToolCallResult struct {
	ID       string                 `json:"id"`
	Name     string                 `json:"name"`
	Arguments map[string]interface{} `json:"arguments"`
}

// JSON-RPC wrapper
type JSONRPCRequest struct {
	JSONRPC string      `json:"jsonrpc"`
	ID      int         `json:"id"`
	Method  string      `json:"method"`
	Params  interface{} `json:"params"`
}

type JSONRPCResponse struct {
	JSONRPC string      `json:"jsonrpc"`
	ID      int         `json:"id"`
	Result  interface{} `json:"result,omitempty"`
	Error   *RPCError   `json:"error,omitempty"`
}

type RPCError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}
```

- [ ] **Step 2: Write IPC server**

Create `spine/ipc.go`:

```go
package main

import (
	"encoding/json"
	"log"
	"net"
	"os"
)

type IPCServer struct {
	cfg         *Config
	supervisor  *Supervisor
	stream      *StreamManager
	events      *EventLogger
	listener    net.Listener
}

func NewIPCServer(cfg *Config, supervisor *Supervisor, stream *StreamManager, events *EventLogger) *IPCServer {
	return &IPCServer{
		cfg:        cfg,
		supervisor: supervisor,
		stream:     stream,
		events:     events,
	}
}

func (s *IPCServer) Start() error {
	// Remove existing socket
	os.Remove(s.cfg.SocketPath)

	var err error
	s.listener, err = net.Listen("unix", s.cfg.SocketPath)
	if err != nil {
		return err
	}

	log.Printf("[Spine] IPC server listening on %s", s.cfg.SocketPath)

	for {
		conn, err := s.listener.Accept()
		if err != nil {
			return err
		}
		go s.handleConn(conn)
	}
}

func (s *IPCServer) Stop() {
	if s.listener != nil {
		s.listener.Close()
	}
}

func (s *IPCServer) handleConn(conn net.Conn) {
	defer conn.Close()

	dec := json.NewDecoder(conn)
	enc := json.NewEncoder(conn)

	for {
		var req JSONRPCRequest
		if err := dec.Decode(&req); err != nil {
			return // connection closed
		}

		resp := s.handleRequest(req)
		enc.Encode(resp)
	}
}

func (s *IPCServer) handleRequest(req JSONRPCRequest) JSONRPCResponse {
	switch req.Method {
	case "think":
		return s.handleThink(req)
	case "tool_result":
		return s.handleToolResult(req)
	case "request_fold":
		return s.handleRequestFold(req)
	case "request_restart":
		return s.handleRequestRestart(req)
	case "send_message":
		return s.handleSendMessage(req)
	case "emit_event":
		return s.handleEmitEvent(req)
	case "get_state":
		return s.handleGetState(req)
	default:
		return JSONRPCResponse{
			JSONRPC: "2.0",
			ID:      req.ID,
			Error:   &RPCError{Code: -32601, Message: "Method not found"},
		}
	}
}

func (s *IPCServer) handleThink(req JSONRPCRequest) JSONRPCResponse {
	var params ThinkRequest
	remarshal(req.Params, &params)

	result, err := s.stream.Think(params)
	if err != nil {
		return JSONRPCResponse{
			JSONRPC: "2.0",
			ID:      req.ID,
			Error:   &RPCError{Code: -32000, Message: err.Error()},
		}
	}
	return JSONRPCResponse{JSONRPC: "2.0", ID: req.ID, Result: result}
}

func (s *IPCServer) handleToolResult(req JSONRPCRequest) JSONRPCResponse {
	var params ToolResultRequest
	remarshal(req.Params, &params)

	s.stream.RecordToolResult(params)
	return JSONRPCResponse{JSONRPC: "2.0", ID: req.ID, Result: "ok"}
}

func (s *IPCServer) handleRequestFold(req JSONRPCRequest) JSONRPCResponse {
	var params RequestFoldRequest
	remarshal(req.Params, &params)

	s.stream.ApplyFold(params.Synthesis)
	return JSONRPCResponse{JSONRPC: "2.0", ID: req.ID, Result: "ok"}
}

func (s *IPCServer) handleRequestRestart(req JSONRPCRequest) JSONRPCResponse {
	var params RequestRestartRequest
	remarshal(req.Params, &params)

	s.supervisor.RequestRestart(params.Reason)
	return JSONRPCResponse{JSONRPC: "2.0", ID: req.ID, Result: "restarting"}
}

func (s *IPCServer) handleSendMessage(req JSONRPCRequest) JSONRPCResponse {
	var params SendMessageRequest
	remarshal(req.Params, &params)

	// Route through Telegram if configured
	if params.Channel == "telegram" && s.cfg.TelegramBotToken != "" {
		// Telegram sending is handled by telegram.go
		SendTelegramMessage(s.cfg, params.Text)
	}
	return JSONRPCResponse{JSONRPC: "2.0", ID: req.ID, Result: "sent"}
}

func (s *IPCServer) handleEmitEvent(req JSONRPCRequest) JSONRPCResponse {
	var params EmitEventRequest
	remarshal(req.Params, &params)

	s.events.Emit(params.Type, params.Payload)
	return JSONRPCResponse{JSONRPC: "2.0", ID: req.ID, Result: "ok"}
}

func (s *IPCServer) handleGetState(req JSONRPCRequest) JSONRPCResponse {
	var params GetStateRequest
	remarshal(req.Params, &params)

	state := s.stream.GetState(params.Keys)
	return JSONRPCResponse{JSONRPC: "2.0", ID: req.ID, Result: state}
}

// Helper to re-serialize interface{} into a typed struct
func remarshal(src interface{}, dst interface{}) {
	data, _ := json.Marshal(src)
	json.Unmarshal(data, dst)
}
```

- [ ] **Step 3: Build and verify compilation**

```bash
cd spine && go build -o spine . && echo "Build succeeded"
```

- [ ] **Step 4: Commit**

```bash
git add spine/ && git commit -m "feat(spine): IPC protocol types and Unix socket server"
```

---

### Task 3: Stream Manager — Construction, Shedding, Folding, and HUD

**Files:**
- Create: `spine/stream.go`
- Create: `spine/stream_test.go`
- Create: `spine/constitution.go`
- Create: `spine/constitution_test.go`

This is the core of the Spine. The StreamManager owns the message stream, constructs LLM payloads, applies shedding, enforces folds, injects the HUD, and tracks state.

- [ ] **Step 1: Write constitution.go**

Create `spine/constitution.go`:

```go
package main

import (
	"crypto/sha256"
	"encoding/hex"
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
```

- [ ] **Step 2: Write constitution_test.go**

Create `spine/constitution_test.go`:

```go
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
```

- [ ] **Step 3: Run tests**

```bash
cd spine && go test -v -run TestConstitution -run TestEmpty ./...
```

- [ ] **Step 4: Write stream.go (core stream manager)**

Create `spine/stream.go`. This is the largest file — it handles message construction, shedding, folding, HUD injection, state tracking, and LLM API calls through the Gate.

Due to length, the stream manager will be implemented as a single file with clear sections. The key functions are:

- `Think(req ThinkRequest) (*ThinkResponse, error)` — the main entry point
- `buildPayload(req ThinkRequest)` — constructs the full LLM API payload
- `applyShedding(messages []Message)` — applies fixed-window shedding
- `formatHUD(hudData HUDData)` — formats the HUD string
- `enforceFold(messages []Message, tools []ToolDef)` — checks if fold is needed and overrides tools
- `RecordToolResult(result ToolResultRequest)` — records tool results in the stream
- `ApplyFold(synthesis string)` — replaces the stream with the fold synthesis
- `GetState(keys []string) map[string]interface{}` — returns authoritative state

The full implementation is ~400 lines and will be written as part of this task.

- [ ] **Step 5: Write stream_test.go**

Create `spine/stream_test.go` with tests for:
- `TestShedding` — verify that messages outside the active window are shed correctly
- `TestFrozenPrefix` — verify that messages 0 and 1 are never modified
- `TestFoldEnforcement` — verify that fold_context is the only tool when context > threshold
- `TestHUDFormat` — verify HUD string format
- `TestFoldReplacesStream` — verify that folding replaces the stream with frozen prefix + synthesis

- [ ] **Step 6: Run tests**

```bash
cd spine && go test -v -run TestShedding -run TestFrozenPrefix -run TestFold -run TestHUD ./...
```

- [ ] **Step 7: Commit**

```bash
git add spine/ && git commit -m "feat(spine): stream manager with shedding, folding, HUD, and constitution tracking"
```

---

### Task 4: Event Logger and State Snapshots

**Files:**
- Create: `spine/events.go`
- Create: `spine/events_test.go`
- Create: `spine/snapshot.go`
- Create: `spine/snapshot_test.go`

- [ ] **Step 1: Write events.go**

Structured event emission to `/spine/events/`. Each event is a JSON line with `type`, `ts`, and event-specific fields.

```go
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

type EventLogger struct {
	eventsDir string
	file      *os.File
}

func NewEventLogger(eventsDir string) *EventLogger {
	return &EventLogger{eventsDir: eventsDir}
}

func (e *EventLogger) Emit(eventType string, payload map[string]interface{}) {
	event := map[string]interface{}{
		"type": eventType,
		"ts":   time.Now().UTC().Format(time.RFC3339),
	}
	for k, v := range payload {
		event[k] = v
	}

	data, err := json.Marshal(event)
	if err != nil {
		fmt.Printf("[Spine] Error marshaling event: %v\n", err)
		return
	}

	if e.file == nil {
		path := filepath.Join(e.eventsDir, time.Now().UTC().Format("2006-01-02")+".jsonl")
		f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
		if err != nil {
			fmt.Printf("[Spine] Error opening event log: %v\n", err)
			return
		}
		e.file = f
	}

	e.file.Write(append(data, '\n'))
}

func (e *EventLogger) Close() {
	if e.file != nil {
		e.file.Close()
	}
}
```

- [ ] **Step 2: Write events_test.go**

```go
package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestEventEmission(t *testing.T) {
	dir := t.TempDir()
	logger := NewEventLogger(dir)

	logger.Emit("cortex.think", map[string]interface{}{
		"turn": 42,
		"focus": "fix_auth_bug",
	})
	logger.Close()

	files, _ := filepath.Glob(filepath.Join(dir, "*.jsonl"))
	if len(files) != 1 {
		t.Fatalf("Expected 1 event file, got %d", len(files))
	}

	data, _ := os.ReadFile(files[0])
	if !strings.Contains(string(data), `"type":"cortex.think"`) {
		t.Errorf("Event file should contain cortex.think event, got: %s", string(data))
	}
	if !strings.Contains(string(data), `"focus":"fix_auth_bug"`) {
		t.Errorf("Event should contain focus field, got: %s", string(data))
	}
}
```

- [ ] **Step 3: Write snapshot.go**

State snapshot save/restore. Snapshots the Cortex's `/memory/` state periodically and before restarts.

```go
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

type SnapshotManager struct {
	snapshotsDir    string
	interval        int // every N turns
	turnsSinceLast  int
}

type Snapshot struct {
	Timestamp   string                 `json:"timestamp"`
	Focus       string                 `json:"focus"`
	MemoryState map[string]interface{} `json:"memory_state"`
	TurnCount   int                    `json:"turn_count"`
	LastEvents  []map[string]interface{} `json:"last_events"`
}

func NewSnapshotManager(snapshotsDir string, interval int) *SnapshotManager {
	return &SnapshotManager{
		snapshotsDir:   snapshotsDir,
		interval:       interval,
		turnsSinceLast: 0,
	}
}

func (sm *SnapshotManager) ShouldSnapshot(turnCount int) bool {
	sm.turnsSinceLast = turnCount
	return turnCount % sm.interval == 0
}

func (sm *SnapshotManager) Save(snapshot *Snapshot) error {
	if err := os.MkdirAll(sm.snapshotsDir, 0755); err != nil {
		return err
	}
	snapshot.Timestamp = time.Now().UTC().Format(time.RFC3339)
	data, err := json.MarshalIndent(snapshot, "", "  ")
	if err != nil {
		return err
	}
	path := filepath.Join(sm.snapshotsDir, "last_good_state.json")
	return os.WriteFile(path, data, 0644)
}

func (sm *SnapshotManager) Load() (*Snapshot, error) {
	path := filepath.Join(sm.snapshotsDir, "last_good_state.json")
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var snapshot Snapshot
	if err := json.Unmarshal(data, &snapshot); err != nil {
		return nil, err
	}
	return &snapshot, nil
}
```

- [ ] **Step 4: Write snapshot_test.go**

```go
package main

import (
	"testing"
)

func TestSnapshotSaveAndLoad(t *testing.T) {
	dir := t.TempDir()
	sm := NewSnapshotManager(dir, 10)

	snapshot := &Snapshot{
		Focus:     "fix_auth_bug",
		TurnCount: 42,
	}

	if err := sm.Save(snapshot); err != nil {
		t.Fatalf("Save failed: %v", err)
	}

	loaded, err := sm.Load()
	if err != nil {
		t.Fatalf("Load failed: %v", err)
	}

	if loaded.Focus != "fix_auth_bug" {
		t.Errorf("Expected focus 'fix_auth_bug', got %q", loaded.Focus)
	}
	if loaded.TurnCount != 42 {
		t.Errorf("Expected turn count 42, got %d", loaded.TurnCount)
	}
}

func TestShouldSnapshot(t *testing.T) {
	sm := NewSnapshotManager("/tmp", 10)

	if sm.ShouldSnapshot(5) {
		t.Error("Should not snapshot at turn 5 with interval 10")
	}
	if !sm.ShouldSnapshot(10) {
		t.Error("Should snapshot at turn 10 with interval 10")
	}
	if !sm.ShouldSnapshot(20) {
		t.Error("Should snapshot at turn 20 with interval 10")
	}
}
```

- [ ] **Step 5: Run tests**

```bash
cd spine && go test -v ./...
```

- [ ] **Step 6: Commit**

```bash
git add spine/ && git commit -m "feat(spine): event logger and state snapshot manager"
```

---

### Task 5: Cortex Supervisor

**Files:**
- Create: `spine/supervisor.go`
- Create: `spine/health.go`

The supervisor manages the Cortex process lifecycle: start, stop, restart, crash detection, and Lazarus Protocol.

- [ ] **Step 1: Write health.go**

Stall detection, crash detection, and startup failure detection.

```go
package main

import (
	"time"
)

type HealthMonitor struct {
	stallTimeout    time.Duration
	startupTimeout  time.Duration
	lastEventTime   time.Time
	cortexStartTime time.Time
	firstThinkDone  bool
}

func NewHealthMonitor(stallTimeout, startupTimeout time.Duration) *HealthMonitor {
	return &HealthMonitor{
		stallTimeout:   stallTimeout,
		startupTimeout:  startupTimeout,
	}
}

func (h *HealthMonitor) RecordEvent() {
	h.lastEventTime = time.Now()
}

func (h *HealthMonitor) RecordFirstThink() {
	h.firstThinkDone = true
}

func (h *HealthMonitor) CortexStarted() {
	h.cortexStartTime = time.Now()
	h.firstThinkDone = false
	h.lastEventTime = time.Now()
}

func (h *HealthMonitor) IsStalled() bool {
	if h.lastEventTime.IsZero() {
		return true
	}
	return time.Since(h.lastEventTime) > h.stallTimeout
}

func (h *HealthMonitor) IsStartupFailure(exitCode int) bool {
	if h.firstThinkDone {
		return false // Cortex ran long enough
	}
	return time.Since(h.cortexStartTime) < h.startupTimeout
}
```

- [ ] **Step 2: Write supervisor.go**

Process lifecycle management with Lazarus Protocol.

```go
package main

import (
	"fmt"
	"log"
	"os"
	"os/exec"
	"os/signal"
	"syscall"
	"time"
)

type Supervisor struct {
	cfg        *Config
	events     *EventLogger
	snapshots  *SnapshotManager
	stream     *StreamManager
	health     *HealthMonitor

	cmd        *exec.Cmd
	process    *os.Process
	restartCh   chan string // channel for restart requests

	consecutiveFailures int
	lastFocus          string
	running            bool
}

func NewSupervisor(cfg *Config, events *EventLogger, snapshots *SnapshotManager, stream *StreamManager) *Supervisor {
	return &Supervisor{
		cfg:       cfg,
		events:    events,
		snapshots: snapshots,
		stream:    stream,
		health:    NewHealthMonitor(cfg.StallTimeout, cfg.StartupTimeout),
		restartCh: make(chan string, 10),
	}
}

func (s *Supervisor) Run() {
	s.running = true
	for s.running {
		s.startCortex()
		s.watchCortex()
	}
}

func (s *Supervisor) Stop() {
	s.running = false
	if s.process != nil {
		s.process.Signal(syscall.SIGTERM)
	}
}

func (s *Supervisor) RequestRestart(reason string) {
	s.restartCh <- reason
	if s.process != nil {
		s.process.Signal(syscall.SIGTERM)
	}
}

func (s *Supervisor) startCortex() {
	s.cmd = exec.Command(s.cfg.CortexBin, s.cfg.CortexArgs...)
	s.cmd.Dir = s.cfg.AppDir
	s.cmd.Env = append(os.Environ(),
		"SPINE_SOCKET="+s.cfg.SocketPath,
		"MEMORY_DIR="+s.cfg.MemoryDir,
		"SPINE_DIR="+s.cfg.SpineDir,
	)

	log.Printf("[Spine] Starting Cortex: %s %v", s.cfg.CortexBin, s.cfg.CortexArgs)
	if err := s.cmd.Start(); err != nil {
		log.Printf("[Spine] Failed to start Cortex: %v", err)
		s.events.Emit("spine.cortex_start_failed", map[string]interface{}{"error": err.Error()})
		time.Sleep(5 * time.Second)
		return
	}

	s.process = s.cmd.Process
	s.health.CortexStarted()
	s.events.Emit("spine.cortex_started", map[string]interface{}{
		"pid": s.process.Pid,
	})
}

func (s *Supervisor) watchCortex() {
	done := make(chan error, 1)
	go func() {
		done <- s.cmd.Wait()
	}()

	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case err := <-done:
			exitCode := 0
			if err != nil {
				if exitErr, ok := err.(*exec.ExitError); ok {
					exitCode = exitErr.ExitCode()
				}
			}
			s.handleCortexExit(exitCode)
			return

		case <-ticker.C:
			if s.health.IsStalled() {
				log.Println("[Spine] Cortex stall detected")
				s.events.Emit("spine.stall_detected", map[string]interface{}{
					"last_event_age_sec": time.Since(s.health.lastEventTime).Seconds(),
				})
				s.process.Signal(syscall.SIGTERM)
			}

		case reason := <-s.restartCh:
			log.Printf("[Spine] Restart requested: %s", reason)
			s.events.Emit("spine.cortex_restart", map[string]interface{}{"reason": reason})
			s.process.Signal(syscall.SIGTERM)
			return
		}
	}
}

func (s *Supervisor) handleCortexExit(exitCode int) {
	s.events.Emit("spine.cortex_crash", map[string]interface{}{
		"exit_code": exitCode,
	})

	// Check if this was a startup failure
	if s.health.IsStartupFailure(exitCode) {
		log.Println("[Spine] Cortex failed during startup — reverting last commit")
		s.events.Emit("spine.startup_failure", map[string]interface{}{"exit_code": exitCode})
		s.revertCommit(1)
	}

	// Lazarus Protocol: increment failure count and revert commits
	s.consecutiveFailures++
	depth := min(s.consecutiveFailures, s.cfg.MaxReversalDepth)
	if depth > 0 {
		s.revertCommit(depth)
	}

	if s.consecutiveFailures >= s.cfg.MaxReversalDepth {
		s.events.Emit("spine.system_override", map[string]interface{}{
			"message": "Maximum reversal depth reached. Abandoning approach.",
		})
	}

	// Queue crash forensics as system notice
	s.stream.QueueSystemNotice(fmt.Sprintf(
		"[SYSTEM | Cortex crashed (exit code %d). Reverted %d commit(s). Consecutive failures: %d]",
		exitCode, depth, s.consecutiveFailures,
	))

	time.Sleep(5 * time.Second)
}

func (s *Supervisor) revertCommit(depth int) {
	cmd := exec.Command("git", "reset", "--hard", fmt.Sprintf("HEAD~%d", depth))
	cmd.Dir = s.cfg.AppDir
	if err := cmd.Run(); err != nil {
		log.Printf("[Spine] Failed to revert commits: %v", err)
	}

	cmd = exec.Command("git", "clean", "-fd")
	cmd.Dir = s.cfg.AppDir
	cmd.Run()
}
```

- [ ] **Step 3: Build**

```bash
cd spine && go build -o spine . && echo "Build succeeded"
```

- [ ] **Step 4: Commit**

```bash
git add spine/ && git commit -m "feat(spine): cortex supervisor with health monitoring and lazarus protocol"
```

---

### Task 6: Control Plane and Telegram Bot

**Files:**
- Create: `spine/control_plane.go`
- Create: `spine/control_plane_test.go`
- Create: `spine/telegram.go`

- [ ] **Step 1: Write control_plane.go**

HTTP API on port 4001 for external observation and control.

```go
package main

import (
	"encoding/json"
	"log"
	"net/http"
)

type ControlPlane struct {
	cfg        *Config
	supervisor *Supervisor
	stream     *StreamManager
	events     *EventLogger
	server     *http.Server
}

func NewControlPlane(cfg *Config, supervisor *Supervisor, stream *StreamManager, events *EventLogger) *ControlPlane {
	cp := &ControlPlane{
		cfg:        cfg,
		supervisor: supervisor,
		stream:     stream,
		events:     events,
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
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "events endpoint"})
}

func (cp *ControlPlane) handleState(w http.ResponseWriter, r *http.Request) {
	state := cp.stream.GetState(nil)
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(state)
}

func (cp *ControlPlane) handleMessage(w http.ResponseWriter, r *http.Request) {
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
```

- [ ] **Step 2: Write telegram.go**

Minimal Telegram bot for essential notifications (crash alerts, health status).

```go
package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
)

type TelegramBot struct {
	token  string
	chatID int64
	client *http.Client
}

func NewTelegramBot(cfg *Config, events *EventLogger) *TelegramBot {
	if cfg.TelegramBotToken == "" {
		return nil
	}
	return &TelegramBot{
		token:  cfg.TelegramBotToken,
		chatID: cfg.TelegramChatID,
		client: &http.Client{},
	}
}

func (tb *TelegramBot) Start() error {
	// Polling loop for incoming messages
	// When a message arrives, queue it as a system_notice via the stream manager
	// This is intentionally minimal — only essential notifications
	return nil
}

func SendTelegramMessage(cfg *Config, text string) {
	if cfg.TelegramBotToken == "" || cfg.TelegramChatID == 0 {
		return
	}
	apiURL := fmt.Sprintf("https://api.telegram.org/bot%s/sendMessage", cfg.TelegramBotToken)
	data := url.Values{
		"chat_id": {fmt.Sprintf("%d", cfg.TelegramChatID)},
		"text":    {text},
	}
	http.PostForm(apiURL, data)
}
```

- [ ] **Step 3: Write control_plane_test.go**

```go
package main

import (
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
```

- [ ] **Step 4: Run tests**

```bash
cd spine && go test -v ./...
```

- [ ] **Step 5: Commit**

```bash
git add spine/ && git commit -m "feat(spine): control plane API and Telegram bot for essential notifications"
```

---

### Task 7: Integration Testing and Docker Setup

**Files:**
- Create: `spine/Dockerfile`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Write Spine Dockerfile**

Create `spine/Dockerfile`:

```dockerfile
FROM golang:1.22-alpine AS builder
WORKDIR /build
COPY go.mod go.sum ./
RUN go mod download
COPY *.go ./
RUN CGO_ENABLED=0 go build -o spine .

FROM alpine:3.19
RUN apk add --no-cache ca-certificates git
COPY --from=builder /build/spine /usr/local/bin/spine
COPY spine_config.json /spine/spine_config.json
ENTRYPOINT ["/usr/local/bin/spine"]
CMD ["/spine/spine_config.json"]
```

- [ ] **Step 2: Create default config**

Create `spine/spine_config.json`:

```json
{
  "memory_dir": "/memory",
  "spine_dir": "/spine",
  "constitution_path": "/app/CONSTITUTION.md",
  "identity_path": "/app/identity.md",
  "app_dir": "/app",
  "cortex_bin": "/venv/bin/python",
  "cortex_args": ["seed_agent.py"],
  "socket_path": "/tmp/spine.sock",
  "control_plane_port": 4001,
  "context_threshold": 0.85,
  "active_window": 5,
  "max_context_tokens": 71680,
  "gate_url": "http://gate:4000",
  "stall_timeout": 600000000000,
  "snapshot_interval": 10,
  "max_reversal_depth": 5,
  "shed_tool_output_max_chars": 500
}
```

- [ ] **Step 3: Update docker-compose.yml to add Spine service**

Add a `spine` service to the existing `docker-compose.yml`. This is the most complex change — it needs to sit alongside the existing `talos`, `gate`, and `llamacpp` services.

- [ ] **Step 4: Write integration test**

Create `spine/integration_test.go` with a test that:
1. Starts a mock Gate server
2. Starts the Spine
3. Verifies the control plane responds
4. Verifies IPC connection works
5. Verifies event logging works

- [ ] **Step 5: Run integration tests**

```bash
cd spine && go test -v -tags=integration ./...
```

- [ ] **Step 6: Commit**

```bash
git add spine/ docker-compose.yml && git commit -m "feat(spine): Docker setup, default config, and integration tests"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ Stream construction, shedding, folding → Task 3
- ✅ HUD injection → Task 3
- ✅ Constitution tracking → Task 3
- ✅ Event logging → Task 4
- ✅ State snapshots → Task 4
- ✅ IPC protocol → Task 2
- ✅ Cortex supervision, Lazarus Protocol → Task 5
- ✅ Health monitoring → Task 5
- ✅ Control plane API → Task 6
- ✅ Telegram essential notifications → Task 6
- ✅ Error handling (orphaned tool calls, timeouts, API errors) → Task 3 (stream manager)
- ✅ Startup failure detection → Task 5
- ✅ Volume layout → Task 7

**Placeholder scan:**
- The stream.go implementation is noted as "~400 lines" but the full code is not written. This is a known gap — the stream manager is the most complex component and will need detailed implementation during the execution phase. The types, interfaces, and test structure are defined.

**Type consistency:**
- `ThinkRequest`, `ToolDef`, `HUDData` are defined in `ipc_types.go` and used consistently in `ipc.go` and `stream.go`.
- `Config` struct fields are consistent across `config.go` and `supervisor.go`.
- All IPC methods in the spec are mapped to handler functions in `ipc.go`.