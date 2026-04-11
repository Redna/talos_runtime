package main

import (
	"fmt"
	"log"
	"os"
	"os/exec"
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
	restartCh  chan string // channel for restart requests

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

	// Reset failure counter if cortex ran past startup timeout (stable run)
	if s.health.firstThinkDone {
		s.consecutiveFailures = 0
	}

	// Check if this was a startup failure
	if s.health.IsStartupFailure(exitCode) {
		log.Println("[Spine] Cortex failed during startup — reverting last commit")
		s.events.Emit("spine.startup_failure", map[string]interface{}{"exit_code": exitCode})
		s.consecutiveFailures++
		s.revertCommit(1)

		s.stream.QueueSystemNotice(fmt.Sprintf(
			"[SYSTEM | Cortex startup failure (exit code %d). Reverted 1 commit. Consecutive failures: %d]",
			exitCode, s.consecutiveFailures,
		))

		time.Sleep(5 * time.Second)
		return
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
