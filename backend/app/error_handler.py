"""
FastAPI error handler for consistent API error responses.
Catches all APIError exceptions and converts them to the unified ApiResponse envelope.
"""

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
import logging

from app.exceptions import APIError, convert_stdlib_exception
from app.helpers.response_helpers import create_error_response

logger = logging.getLogger(__name__)

def setup_error_handlers(app: FastAPI):
    """Setup error handlers for the FastAPI application."""
    
    @app.exception_handler(APIError)
    async def api_error_handler(request: Request, exc: APIError):
        """Handle custom APIError exceptions."""
        logger.error(f"APIError [{exc.code}]: {exc.message} - {exc.details}")
        
        response = create_error_response(message=exc.message, code=exc.code, details=exc.details)
        return JSONResponse(
            status_code=exc.status_code,
            content=response.model_dump()
        )
    
    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        """Handle Pydantic validation errors."""
        error_details = {"validation_errors": exc.errors()}
        
        response = create_error_response(
            message="Request validation failed",
            code="VALIDATION_ERROR",
            details=error_details
        )
        return JSONResponse(
            status_code=422,
            content=response.model_dump()
        )
    
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        """Handle FastAPI HTTP exceptions."""
        response = create_error_response(message=exc.detail, code="HTTP_EXCEPTION", details=None)
        return JSONResponse(
            status_code=exc.status_code,
            content=response.model_dump()
        )
    
    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        """Handle unexpected exceptions by converting to APIError."""
        logger.error(f"Unexpected error: {str(exc)}", exc_info=True)
        
        api_error = convert_stdlib_exception(exc)
        response = create_error_response(
            message=api_error.message, 
            code=api_error.code, 
            details=api_error.details
        )
        
        return JSONResponse(
            status_code=api_error.status_code,
            content=response.model_dump()
        )
