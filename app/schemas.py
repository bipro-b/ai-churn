"""
Request/response schemas.

Senior note: validation at the edge is non-negotiable. A model that receives
garbage input produces confident garbage output. Pydantic enforces types and
ranges *before* anything reaches the model, and gives clients clear 422 errors
instead of 500s. This is the contract between your service and its callers.
"""

from enum import Enum

from pydantic import BaseModel, Field


class ContractType(str, Enum):
    month_to_month = "month-to-month"
    one_year = "one-year"
    two_year = "two-year"


class PaymentMethod(str, Enum):
    electronic_check = "electronic-check"
    mailed_check = "mailed-check"
    bank_transfer = "bank-transfer"
    credit_card = "credit-card"


class InternetService(str, Enum):
    dsl = "dsl"
    fiber = "fiber"
    none = "none"


class CustomerFeatures(BaseModel):
    tenure_months: int = Field(..., ge=0, le=120, description="Months as a customer")
    monthly_charges: float = Field(..., ge=0, le=1000)
    total_charges: float = Field(..., ge=0)
    num_support_tickets: int = Field(..., ge=0, le=100)
    contract_type: ContractType
    payment_method: PaymentMethod
    internet_service: InternetService

    model_config = {
        "json_schema_extra": {
            "example": {
                "tenure_months": 3,
                "monthly_charges": 95.5,
                "total_charges": 286.5,
                "num_support_tickets": 4,
                "contract_type": "month-to-month",
                "payment_method": "electronic-check",
                "internet_service": "fiber",
            }
        }
    }


class PredictionResponse(BaseModel):
    churn_probability: float = Field(..., ge=0, le=1)
    churn_prediction: bool
    risk_band: str  # low / medium / high
    model_version: str


class BatchPredictionRequest(BaseModel):
    customers: list[CustomerFeatures] = Field(..., min_length=1, max_length=1000)


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    model_version: str
