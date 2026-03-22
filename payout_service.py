"""
PaySuite Payout Service
Handles automatic payouts to affiliates via PaySuite API
"""
import os
import requests
from typing import Dict, Any, Optional
from datetime import datetime


class PayoutService:
    """Service for managing PaySuite payouts"""

    def __init__(self):
        self.base_url = "https://paysuite.tech/api/v1"

    @property
    def headers(self):
        # Read token at request time so load_dotenv() has already run
        token = os.getenv("PAYSUITE_API_TOKEN", "")
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    def create_payout(
        self,
        amount: int,
        mobile: str,
        holder_name: str,
        reference: str,
        description: Optional[str] = None,
        method: str = "mpesa"
    ) -> Dict[str, Any]:
        """
        Create a payout request via PaySuite API

        Args:
            amount: Amount in MZN (10 - 1,000,000)
            mobile: Beneficiary phone number (9 digits, e.g., "850219049")
            holder_name: Beneficiary name
            reference: Unique reference (max 30 chars)
            description: Optional description (max 255 chars)
            method: Payment method (mpesa, emola, mkesh, bank_transfer)

        Returns:
            Dict with payout details or error
        """
        # Clean mobile number (remove +258 prefix if present)
        clean_mobile = mobile.replace("+258", "").replace(" ", "")

        # Prepare payload
        payload = {
            "amount": str(amount),
            "currency": "MZN",
            "reference": reference,
            "method": method,
            "beneficiary": {
                "phone": clean_mobile,
                "holder": holder_name
            }
        }

        if description:
            payload["description"] = description[:255]

        try:
            response = requests.post(
                f"{self.base_url}/payouts",
                headers=self.headers,
                json=payload,
                timeout=30
            )

            if response.status_code == 201:
                # Successful payout creation
                data = response.json()
                return {
                    "success": True,
                    "payout_id": data["data"]["id"],
                    "status": data["data"]["status"],
                    "reference": data["data"]["reference"],
                    "amount": data["data"]["amount"],
                    "method": data["data"]["method"],
                    "created_at": data["data"]["created_at"],
                    "raw_response": data
                }
            else:
                # Error response
                error_data = response.json() if response.text else {}
                return {
                    "success": False,
                    "error": "payout_failed",
                    "message": error_data.get("message", f"HTTP {response.status_code}"),
                    "status_code": response.status_code,
                    "raw_response": error_data
                }

        except requests.exceptions.Timeout:
            return {
                "success": False,
                "error": "timeout",
                "message": "PaySuite API request timed out"
            }
        except requests.exceptions.ConnectionError:
            return {
                "success": False,
                "error": "connection_error",
                "message": "Could not connect to PaySuite API"
            }
        except Exception as e:
            return {
                "success": False,
                "error": "unknown_error",
                "message": str(e)
            }

    def check_payout_status(self, payout_id: str) -> Dict[str, Any]:
        """
        Check status of a payout by its ID

        Args:
            payout_id: PaySuite payout UUID

        Returns:
            Dict with payout status details
        """
        try:
            response = requests.get(
                f"{self.base_url}/payouts/{payout_id}",
                headers=self.headers,
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                return {
                    "success": True,
                    "payout_id": data["data"]["id"],
                    "status": data["data"]["status"],
                    "reference": data["data"]["reference"],
                    "amount": data["data"]["amount"],
                    "method": data["data"]["method"],
                    "created_at": data["data"]["created_at"],
                    "raw_response": data
                }
            else:
                error_data = response.json() if response.text else {}
                return {
                    "success": False,
                    "error": "status_check_failed",
                    "message": error_data.get("message", f"HTTP {response.status_code}"),
                    "status_code": response.status_code
                }

        except requests.exceptions.Timeout:
            return {
                "success": False,
                "error": "timeout",
                "message": "PaySuite API request timed out"
            }
        except requests.exceptions.ConnectionError:
            return {
                "success": False,
                "error": "connection_error",
                "message": "Could not connect to PaySuite API"
            }
        except Exception as e:
            return {
                "success": False,
                "error": "unknown_error",
                "message": str(e)
            }

    def list_payouts(self, page: int = 1, limit: int = 20) -> Dict[str, Any]:
        """
        List all payouts with pagination

        Args:
            page: Page number (default 1)
            limit: Items per page (default 20)

        Returns:
            Dict with list of payouts
        """
        try:
            response = requests.get(
                f"{self.base_url}/payouts",
                headers=self.headers,
                params={"page": page, "limit": limit},
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                return {
                    "success": True,
                    "payouts": data.get("data", []),
                    "raw_response": data
                }
            else:
                error_data = response.json() if response.text else {}
                return {
                    "success": False,
                    "error": "list_failed",
                    "message": error_data.get("message", f"HTTP {response.status_code}"),
                    "status_code": response.status_code
                }

        except Exception as e:
            return {
                "success": False,
                "error": "unknown_error",
                "message": str(e)
            }


# Global payout service instance
payout_service = PayoutService()
