from __future__ import annotations

from typing import Any


SWAGGER_UI_HTML = """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>YT Short Clipper API Docs</title>
  <link rel=\"stylesheet\" href=\"https://unpkg.com/swagger-ui-dist@5/swagger-ui.css\">
  <style>
    body { margin: 0; background: #0b1020; }
    #swagger-ui { max-width: 1200px; margin: 0 auto; }
  </style>
</head>
<body>
  <div id=\"swagger-ui\"></div>
  <script src=\"https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js\"></script>
  <script>
    window.ui = SwaggerUIBundle({
      url: '__OPENAPI_URL__',
      dom_id: '#swagger-ui',
      deepLinking: true,
      presets: [SwaggerUIBundle.presets.apis],
    });
  </script>
</body>
</html>
"""


def build_openapi_spec(server_url: str) -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "YT Short Clipper API",
            "version": "1.0.0",
            "description": (
                "HTTP integration API for the GUI-first YT Short Clipper app. "
                "The API shares the same backend service layer as the desktop/webview GUI."
            ),
        },
        "servers": [{"url": server_url}],
        "tags": [
            {"name": "system", "description": "Health and discovery endpoints"},
            {"name": "config", "description": "AI provider configuration"},
            {"name": "providers", "description": "Provider validation and model loading"},
            {"name": "jobs", "description": "Background processing jobs"},
            {"name": "sessions", "description": "Saved highlight sessions"},
        ],
        "paths": {
            "/health": {
                "get": {
                    "tags": ["system"],
                    "summary": "Health check",
                    "responses": {
                        "200": {
                            "description": "API is reachable",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/HealthResponse"}
                                }
                            },
                        }
                    },
                }
            },
            "/api/openapi.json": {
                "get": {
                    "tags": ["system"],
                    "summary": "OpenAPI document",
                    "responses": {
                        "200": {
                            "description": "OpenAPI 3.1 spec",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "additionalProperties": True}
                                }
                            },
                        }
                    },
                }
            },
            "/docs": {
                "get": {
                    "tags": ["system"],
                    "summary": "Swagger UI docs",
                    "responses": {
                        "200": {"description": "Swagger UI HTML page"}
                    },
                }
            },
            "/api/config/ai": {
                "get": {
                    "tags": ["config"],
                    "summary": "Get AI provider settings",
                    "responses": {
                        "200": {
                            "description": "Current AI configuration",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/AiConfigResponse"}
                                }
                            },
                        }
                    },
                },
                "post": {
                    "tags": ["config"],
                    "summary": "Save AI provider settings",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/AiConfigSaveRequest"}
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Settings saved",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/StatusResponse"}
                                }
                            },
                        }
                    },
                },
            },
            "/api/providers/validate": {
                "post": {
                    "tags": ["providers"],
                    "summary": "Validate provider credentials",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ProviderRequest"}
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Validation result",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/StatusResponse"}
                                }
                            },
                        }
                    },
                }
            },
            "/api/providers/models": {
                "post": {
                    "tags": ["providers"],
                    "summary": "List provider models",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ProviderRequest"}
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Available model ids",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ModelsResponse"}
                                }
                            },
                        }
                    },
                }
            },
            "/api/jobs": {
                "get": {
                    "tags": ["jobs"],
                    "summary": "List jobs",
                    "responses": {
                        "200": {
                            "description": "Known background jobs",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "jobs": {
                                                "type": "array",
                                                "items": {"$ref": "#/components/schemas/Job"},
                                            }
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/api/jobs/{job_id}": {
                "get": {
                    "tags": ["jobs"],
                    "summary": "Get one job",
                    "parameters": [
                        {
                            "name": "job_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Job details",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Job"}
                                }
                            },
                        },
                        "404": {"$ref": "#/components/responses/ErrorResponse"},
                    },
                }
            },
            "/api/jobs/{job_id}/cancel": {
                "post": {
                    "tags": ["jobs"],
                    "summary": "Cancel a running job",
                    "parameters": [
                        {
                            "name": "job_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Updated job state",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Job"}
                                }
                            },
                        },
                        "404": {"$ref": "#/components/responses/ErrorResponse"},
                    },
                }
            },
            "/api/jobs/full-process": {
                "post": {
                    "tags": ["jobs"],
                    "summary": "Run the full one-shot pipeline",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/FullProcessRequest"}
                            }
                        },
                    },
                    "responses": {
                        "202": {
                            "description": "Job queued",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Job"}
                                }
                            },
                        },
                        "400": {"$ref": "#/components/responses/ErrorResponse"},
                    },
                }
            },
            "/api/jobs/find-highlights": {
                "post": {
                    "tags": ["jobs"],
                    "summary": "Download a video and find highlight candidates",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/FindHighlightsRequest"}
                            }
                        },
                    },
                    "responses": {
                        "202": {
                            "description": "Job queued",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Job"}
                                }
                            },
                        },
                        "400": {"$ref": "#/components/responses/ErrorResponse"},
                    },
                }
            },
            "/api/jobs/process-selected": {
                "post": {
                    "tags": ["jobs"],
                    "summary": "Process selected highlights from a saved session",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ProcessSelectedRequest"}
                            }
                        },
                    },
                    "responses": {
                        "202": {
                            "description": "Job queued",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Job"}
                                }
                            },
                        },
                        "400": {"$ref": "#/components/responses/ErrorResponse"},
                    },
                }
            },
            "/api/sessions": {
                "get": {
                    "tags": ["sessions"],
                    "summary": "List saved sessions",
                    "responses": {
                        "200": {
                            "description": "Saved sessions",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "sessions": {
                                                "type": "array",
                                                "items": {"$ref": "#/components/schemas/Session"},
                                            }
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/api/sessions/{session_id}": {
                "get": {
                    "tags": ["sessions"],
                    "summary": "Get one saved session",
                    "parameters": [
                        {
                            "name": "session_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Session payload",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Session"}
                                }
                            },
                        },
                        "404": {"$ref": "#/components/responses/ErrorResponse"},
                    },
                }
            },
        },
        "components": {
            "responses": {
                "ErrorResponse": {
                    "description": "Error response",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                        }
                    },
                }
            },
            "schemas": {
                "HealthResponse": {
                    "type": "object",
                    "properties": {"status": {"type": "string", "example": "ok"}},
                    "required": ["status"],
                },
                "StatusResponse": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "example": "saved"},
                        "message": {"type": "string"},
                    },
                    "required": ["status"],
                },
                "ErrorResponse": {
                    "type": "object",
                    "properties": {"error": {"type": "string"}},
                    "required": ["error"],
                },
                "ProviderConfig": {
                    "type": "object",
                    "properties": {
                        "base_url": {"type": "string", "example": "https://api.openai.com/v1"},
                        "api_key": {"type": "string", "example": "sk-..."},
                        "model": {"type": "string", "example": "gpt-4.1"},
                    },
                    "additionalProperties": True,
                },
                "AiConfigSaveRequest": {
                    "type": "object",
                    "properties": {
                        "_provider_type": {"type": "string", "example": "openai"},
                        "highlight_finder": {"$ref": "#/components/schemas/ProviderConfig"},
                        "caption_maker": {"$ref": "#/components/schemas/ProviderConfig"},
                        "hook_maker": {"$ref": "#/components/schemas/ProviderConfig"},
                        "youtube_title_maker": {"$ref": "#/components/schemas/ProviderConfig"},
                    },
                    "additionalProperties": {"$ref": "#/components/schemas/ProviderConfig"},
                },
                "AiConfigResponse": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "example": "ok"},
                        "provider_type": {"type": "string", "example": "openai"},
                        "data": {
                            "type": "object",
                            "additionalProperties": {"$ref": "#/components/schemas/ProviderConfig"},
                        },
                    },
                    "required": ["status", "provider_type", "data"],
                },
                "ProviderRequest": {
                    "type": "object",
                    "properties": {
                        "base_url": {"type": "string", "example": "https://api.openai.com/v1"},
                        "api_key": {"type": "string", "example": "sk-..."},
                    },
                    "required": ["base_url", "api_key"],
                },
                "ModelsResponse": {
                    "type": "object",
                    "properties": {
                        "models": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["models"],
                },
                "FullProcessRequest": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "example": "https://www.youtube.com/watch?v=VIDEO_ID"},
                        "num_clips": {"type": "integer", "default": 5, "minimum": 1},
                        "add_captions": {"type": "boolean", "default": True},
                        "add_hook": {"type": "boolean", "default": False},
                        "subtitle_language": {"type": "string", "default": "id"},
                    },
                    "required": ["url"],
                },
                "FindHighlightsRequest": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "example": "https://www.youtube.com/watch?v=VIDEO_ID"},
                        "num_clips": {"type": "integer", "default": 5, "minimum": 1},
                        "subtitle_language": {"type": "string", "default": "id"},
                    },
                    "required": ["url"],
                },
                "ProcessSelectedRequest": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "session_dir": {"type": "string"},
                        "selected_indexes": {"type": "array", "items": {"type": "integer"}},
                        "selected_highlights": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                        "add_captions": {"type": "boolean", "default": False},
                        "add_hook": {"type": "boolean", "default": False},
                    },
                },
                "TokenUsage": {
                    "type": "object",
                    "properties": {
                        "gpt_input": {"type": "integer"},
                        "gpt_output": {"type": "integer"},
                        "whisper_seconds": {"type": "number"},
                        "tts_chars": {"type": "integer"},
                    },
                },
                "Job": {
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string"},
                        "type": {"type": "string"},
                        "status": {"type": "string", "enum": ["queued", "running", "cancelling", "completed", "failed", "cancelled"]},
                        "progress": {"type": "number"},
                        "message": {"type": "string"},
                        "created_at": {"type": "string", "format": "date-time"},
                        "updated_at": {"type": "string", "format": "date-time"},
                        "finished_at": {"type": ["string", "null"], "format": "date-time"},
                        "input": {"type": "object", "additionalProperties": True},
                        "result": {"type": ["object", "null"], "additionalProperties": True},
                        "error": {"type": ["string", "null"]},
                        "cancel_requested": {"type": "boolean"},
                        "token_usage": {"$ref": "#/components/schemas/TokenUsage"},
                        "logs": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["job_id", "type", "status", "progress", "message", "created_at", "updated_at", "cancel_requested", "token_usage", "logs"],
                },
                "Highlight": {
                    "type": "object",
                    "properties": {
                        "start_time": {"type": "string"},
                        "end_time": {"type": "string"},
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "virality_score": {"type": "integer"},
                        "hook_text": {"type": "string"},
                    },
                    "additionalProperties": True,
                },
                "Session": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "session_dir": {"type": "string"},
                        "clips_dir": {"type": "string"},
                        "video_path": {"type": "string"},
                        "srt_path": {"type": ["string", "null"]},
                        "highlights": {"type": "array", "items": {"$ref": "#/components/schemas/Highlight"}},
                        "video_info": {"type": "object", "additionalProperties": True},
                        "status": {"type": "string"},
                        "created_at": {"type": ["string", "null"], "format": "date-time"},
                        "completed_at": {"type": ["string", "null"], "format": "date-time"},
                        "clips_processed": {"type": ["integer", "null"]},
                    },
                    "required": ["session_id", "session_dir", "highlights"],
                    "additionalProperties": True,
                },
            },
        },
    }


def build_swagger_ui_html(openapi_url: str) -> str:
    return SWAGGER_UI_HTML.replace("__OPENAPI_URL__", openapi_url)
