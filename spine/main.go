package main

import (
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
	log.Println("[Spine] Stopped.")
}
