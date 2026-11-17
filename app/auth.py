"""
Authentication utilities for password hashing and JWT tokens.
"""
from datetime import timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from . import timeutil
from .config import ALGORITHM, settings
from .models import Admin

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash a plain password."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token."""
    to_encode = data.copy()
    
    if expires_delta:
        expire = timeutil.utcnow() + expires_delta
    else:
        expire = timeutil.utcnow() + timedelta(hours=settings.ACCESS_TOKEN_EXPIRE_HOURS)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)
    
    return encoded_jwt


def verify_student_token(token: str) -> int:
    """
    Verify JWT token and extract student ID.
    
    Args:
        token: JWT token string
        
    Returns:
        student_id: ID of the authenticated student
        
    Raises:
        HTTPException: If token is invalid or expired
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        student_id: int = payload.get("sub")
        user_type: str = payload.get("type")
        
        if student_id is None or user_type != "student":
            raise credentials_exception
            
        return int(student_id)
        
    except JWTError:
        raise credentials_exception


def verify_admin_token(token: str) -> str:
    """
    Verify JWT token and extract admin username.
    
    Args:
        token: JWT token string
        
    Returns:
        username: Username of the authenticated admin
        
    Raises:
        HTTPException: If token is invalid or expired
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate admin credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        user_type: str = payload.get("type")
        
        if username is None or user_type != "admin":
            raise credentials_exception
            
        return username
        
    except JWTError:
        raise credentials_exception


def verify_admin_credentials(db: Session, username: str, password: str) -> bool:
    """Verify admin username and password against the admins table."""
    admin = db.query(Admin).filter(Admin.username == username).first()
    if admin is None:
        # Spend comparable time so unknown usernames are not distinguishable by timing.
        pwd_context.dummy_verify()
        return False
    return verify_password(password, admin.hashed_password)
