package main

type TelegramBot struct {
	cfg         *Config
	eventLogger *EventLogger
}

func NewTelegramBot(cfg *Config, eventLogger *EventLogger) *TelegramBot {
	return &TelegramBot{
		cfg:         cfg,
		eventLogger: eventLogger,
	}
}

func (t *TelegramBot) Start() error {
	// Will be implemented in Task 6
	return nil
}
