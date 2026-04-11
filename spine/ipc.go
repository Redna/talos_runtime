package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net"
	"os"
)

type IPCServer struct {
	cfg        *Config
	supervisor *Supervisor
	stream     *StreamManager
	events     *EventLogger
	listener   net.Listener
	done       chan struct{}
}

func NewIPCServer(cfg *Config, supervisor *Supervisor, stream *StreamManager, events *EventLogger) *IPCServer {
	return &IPCServer{
		cfg:        cfg,
		supervisor: supervisor,
		stream:     stream,
		events:     events,
		done:       make(chan struct{}),
	}
}

func (s *IPCServer) Start() error {
	// Remove existing socket
	if err := os.Remove(s.cfg.SocketPath); err != nil && !os.IsNotExist(err) {
		log.Printf("[Spine] Warning: could not remove stale socket %s: %v", s.cfg.SocketPath, err)
	}

	var err error
	s.listener, err = net.Listen("unix", s.cfg.SocketPath)
	if err != nil {
		return err
	}

	log.Printf("[Spine] IPC server listening on %s", s.cfg.SocketPath)

	for {
		conn, err := s.listener.Accept()
		if err != nil {
			select {
			case <-s.done:
				return nil // intentional shutdown
			default:
				return err
			}
		}
		go s.handleConn(conn)
	}
}

func (s *IPCServer) Stop() {
	close(s.done)
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
		if err := enc.Encode(resp); err != nil {
			log.Printf("[Spine] IPC encode error: %v", err)
			return
		}
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
	if err := remarshal(req.Params, &params); err != nil {
		return JSONRPCResponse{JSONRPC: "2.0", ID: req.ID, Error: &RPCError{Code: -32602, Message: err.Error()}}
	}

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
	if err := remarshal(req.Params, &params); err != nil {
		return JSONRPCResponse{JSONRPC: "2.0", ID: req.ID, Error: &RPCError{Code: -32602, Message: err.Error()}}
	}

	s.stream.RecordToolResult(params)
	return JSONRPCResponse{JSONRPC: "2.0", ID: req.ID, Result: "ok"}
}

func (s *IPCServer) handleRequestFold(req JSONRPCRequest) JSONRPCResponse {
	var params RequestFoldRequest
	if err := remarshal(req.Params, &params); err != nil {
		return JSONRPCResponse{JSONRPC: "2.0", ID: req.ID, Error: &RPCError{Code: -32602, Message: err.Error()}}
	}

	s.stream.ApplyFold(params.Synthesis)
	return JSONRPCResponse{JSONRPC: "2.0", ID: req.ID, Result: "ok"}
}

func (s *IPCServer) handleRequestRestart(req JSONRPCRequest) JSONRPCResponse {
	var params RequestRestartRequest
	if err := remarshal(req.Params, &params); err != nil {
		return JSONRPCResponse{JSONRPC: "2.0", ID: req.ID, Error: &RPCError{Code: -32602, Message: err.Error()}}
	}

	s.supervisor.RequestRestart(params.Reason)
	return JSONRPCResponse{JSONRPC: "2.0", ID: req.ID, Result: "restarting"}
}

func (s *IPCServer) handleSendMessage(req JSONRPCRequest) JSONRPCResponse {
	var params SendMessageRequest
	if err := remarshal(req.Params, &params); err != nil {
		return JSONRPCResponse{JSONRPC: "2.0", ID: req.ID, Error: &RPCError{Code: -32602, Message: err.Error()}}
	}

	// Route through Telegram if configured
	if params.Channel == "telegram" && s.cfg.TelegramBotToken != "" {
		SendTelegramMessage(s.cfg, params.Text)
	}
	return JSONRPCResponse{JSONRPC: "2.0", ID: req.ID, Result: "sent"}
}

func (s *IPCServer) handleEmitEvent(req JSONRPCRequest) JSONRPCResponse {
	var params EmitEventRequest
	if err := remarshal(req.Params, &params); err != nil {
		return JSONRPCResponse{JSONRPC: "2.0", ID: req.ID, Error: &RPCError{Code: -32602, Message: err.Error()}}
	}

	s.events.Emit(params.Type, params.Payload)
	return JSONRPCResponse{JSONRPC: "2.0", ID: req.ID, Result: "ok"}
}

func (s *IPCServer) handleGetState(req JSONRPCRequest) JSONRPCResponse {
	var params GetStateRequest
	if err := remarshal(req.Params, &params); err != nil {
		return JSONRPCResponse{JSONRPC: "2.0", ID: req.ID, Error: &RPCError{Code: -32602, Message: err.Error()}}
	}

	state := s.stream.GetState(params.Keys)
	return JSONRPCResponse{JSONRPC: "2.0", ID: req.ID, Result: state}
}

// Helper to re-serialize interface{} into a typed struct
func remarshal(src interface{}, dst interface{}) error {
	data, err := json.Marshal(src)
	if err != nil {
		return fmt.Errorf("remarshal encode: %w", err)
	}
	if err := json.Unmarshal(data, dst); err != nil {
		return fmt.Errorf("remarshal decode: %w", err)
	}
	return nil
}