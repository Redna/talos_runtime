package main

type EventLogger struct {
	eventsDir string
}

func NewEventLogger(eventsDir string) *EventLogger {
	return &EventLogger{eventsDir: eventsDir}
}

func (e *EventLogger) Emit(eventType string, payload map[string]interface{}) {
	// Will be implemented in Task 4
}

func (e *EventLogger) Close() {
	// Will be implemented in Task 4
}