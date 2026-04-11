package main

import (
	"encoding/json"
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