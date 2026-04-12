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