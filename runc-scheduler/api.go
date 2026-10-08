package main

import (
	"context"
	"crypto/subtle"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"strings"
	"time"
)

type ControlConfig struct {
	Listen             string `json:"listen"`
	StateFile          string `json:"stateFile"`
	AdminTokenFile     string `json:"adminTokenFile"`
	NodeTokenFile      string `json:"nodeTokenFile"`
	TLSCert            string `json:"tlsCert,omitempty"`
	TLSKey             string `json:"tlsKey,omitempty"`
	NodeTimeoutSeconds int    `json:"nodeTimeoutSeconds"`
}

func readJSON(path string, v any) error {
	b, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	dec := json.NewDecoder(strings.NewReader(string(b)))
	dec.DisallowUnknownFields()
	return dec.Decode(v)
}
func readToken(path string) (string, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	t := strings.TrimSpace(string(b))
	if len(t) < 32 {
		return "", fmt.Errorf("token in %s must be at least 32 characters", path)
	}
	return t, nil
}
func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(v)
}
func decodeBody(w http.ResponseWriter, r *http.Request, v any) bool {
	dec := json.NewDecoder(http.MaxBytesReader(w, r.Body, 32<<20))
	dec.DisallowUnknownFields()
	if err := dec.Decode(v); err != nil {
		http.Error(w, err.Error(), 400)
		return false
	}
	var extra any
	if err := dec.Decode(&extra); err != io.EOF {
		http.Error(w, "one JSON document required", 400)
		return false
	}
	return true
}
func apiHandler(e *Engine, admin, nodeToken string) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		if r.URL.Path == "/healthz" && r.Method == "GET" {
			writeJSON(w, map[string]string{"status": "ok"})
			return
		}
		expected := admin
		if r.URL.Path == "/v1/heartbeat" {
			expected = nodeToken
		}
		supplied := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		if subtle.ConstantTimeCompare([]byte(supplied), []byte(expected)) != 1 {
			http.Error(w, "unauthorized", 401)
			return
		}
		now := time.Now().UTC()
		var err error
		switch {
		case r.URL.Path == "/v1/state" && r.Method == "GET":
			writeJSON(w, e.snapshot())
			return
		case r.URL.Path == "/v1/heartbeat" && r.Method == "POST":
			var h Heartbeat
			if !decodeBody(w, r, &h) {
				return
			}
			var a Assignment
			a, err = e.heartbeat(h, now)
			if err == nil {
				writeJSON(w, a)
				return
			}
		case r.URL.Path == "/v1/workloads" && r.Method == "POST":
			var workload Workload
			if !decodeBody(w, r, &workload) {
				return
			}
			err = e.apply(workload, now)
		case strings.HasPrefix(r.URL.Path, "/v1/workloads/") && r.Method == "DELETE":
			err = e.remove(strings.TrimPrefix(r.URL.Path, "/v1/workloads/"), now)
		case strings.HasPrefix(r.URL.Path, "/v1/scale/") && r.Method == "POST":
			var x struct {
				Replicas int `json:"replicas"`
			}
			if !decodeBody(w, r, &x) {
				return
			}
			err = e.scale(strings.TrimPrefix(r.URL.Path, "/v1/scale/"), x.Replicas, now)
		case strings.HasPrefix(r.URL.Path, "/v1/nodes/") && r.Method == "PATCH":
			var p NodePatch
			if !decodeBody(w, r, &p) {
				return
			}
			err = e.patchNode(strings.TrimPrefix(r.URL.Path, "/v1/nodes/"), p, now)
		case r.URL.Path == "/v1/exec" && r.Method == "POST":
			var x struct {
				PodID   string   `json:"podId"`
				Command []string `json:"command"`
			}
			if !decodeBody(w, r, &x) {
				return
			}
			var id string
			id, err = e.exec(x.PodID, x.Command, now)
			if err == nil {
				writeJSON(w, map[string]string{"id": id})
				return
			}
		default:
			http.Error(w, "endpoint or method not found", 404)
			return
		}
		if err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		writeJSON(w, map[string]string{"status": "ok"})
	})
}
func runControl(ctx context.Context, c ControlConfig) error {
	if c.Listen == "" {
		c.Listen = "127.0.0.1:8080"
	}
	if c.StateFile == "" {
		return fmt.Errorf("stateFile required")
	}
	if c.NodeTimeoutSeconds < 10 {
		c.NodeTimeoutSeconds = 30
	}
	admin, err := readToken(c.AdminTokenFile)
	if err != nil {
		return err
	}
	nodes, err := readToken(c.NodeTokenFile)
	if err != nil {
		return err
	}
	if admin == nodes {
		return fmt.Errorf("admin and node tokens must differ")
	}
	e, err := newEngine(c.StateFile, time.Duration(c.NodeTimeoutSeconds)*time.Second)
	if err != nil {
		return err
	}
	srv := &http.Server{Addr: c.Listen, Handler: apiHandler(e, admin, nodes), ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 30 * time.Second, WriteTimeout: 30 * time.Second, IdleTimeout: 60 * time.Second, TLSConfig: &tls.Config{MinVersion: tls.VersionTLS12}}
	errors := make(chan error, 2)
	go func() {
		tick := time.NewTicker(time.Second)
		defer tick.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case now := <-tick.C:
				if err := e.tick(now.UTC()); err != nil {
					errors <- fmt.Errorf("state persistence failed: %w", err)
					return
				}
			}
		}
	}()
	go func() {
		log.Printf("control listening on %s", c.Listen)
		if c.TLSCert != "" {
			errors <- srv.ListenAndServeTLS(c.TLSCert, c.TLSKey)
		} else {
			errors <- srv.ListenAndServe()
		}
	}()
	select {
	case <-ctx.Done():
		err = nil
	case err = <-errors:
		if err == http.ErrServerClosed {
			err = nil
		}
	}
	stop, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = srv.Shutdown(stop)
	return err
}

type Client struct {
	URL   string
	Token string
	HTTP  *http.Client
}

func newClient(url, token, ca string) (*Client, error) {
	tlsConfig := &tls.Config{MinVersion: tls.VersionTLS12}
	if ca != "" {
		b, err := os.ReadFile(ca)
		if err != nil {
			return nil, err
		}
		pool, err := x509.SystemCertPool()
		if err != nil {
			pool = x509.NewCertPool()
		}
		if !pool.AppendCertsFromPEM(b) {
			return nil, fmt.Errorf("no certificates in CA file")
		}
		tlsConfig.RootCAs = pool
	}
	return &Client{strings.TrimRight(url, "/"), token, &http.Client{Timeout: 20 * time.Second, Transport: &http.Transport{TLSClientConfig: tlsConfig}}}, nil
}
func (c *Client) request(ctx context.Context, method, path string, in, out any) error {
	var body io.Reader
	if in != nil {
		b, err := json.Marshal(in)
		if err != nil {
			return err
		}
		body = strings.NewReader(string(b))
	}
	r, err := http.NewRequestWithContext(ctx, method, c.URL+path, body)
	if err != nil {
		return err
	}
	r.Header.Set("Authorization", "Bearer "+c.Token)
	r.Header.Set("Content-Type", "application/json")
	resp, err := c.HTTP.Do(r)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode/100 != 2 {
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return fmt.Errorf("HTTP %d: %s", resp.StatusCode, strings.TrimSpace(string(b)))
	}
	if out != nil {
		return json.NewDecoder(io.LimitReader(resp.Body, 64<<20)).Decode(out)
	}
	_, err = io.Copy(io.Discard, resp.Body)
	return err
}
