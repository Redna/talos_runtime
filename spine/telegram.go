package main

import (
	"fmt"
	"net/http"
	"net/url"
)

type TelegramBot struct {
	token  string
	chatID int64
	client *http.Client
}

func NewTelegramBot(cfg *Config, eventLogger *EventLogger) *TelegramBot {
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
	// Polling loop for incoming messages - minimal implementation
	// When a message arrives, queue it as a system_notice via the stream manager
	// For now, just log that the bot started
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