import time, json, re, threading
import concurrent.futures
from tqdm import tqdm
from dotenv import load_dotenv

from google import genai
from google.genai import types

from load_balancer import LoadBalancer
from prompts import SYSTEM_PROMPT, SERVICES_OFFER_PROMPT, SYSTEM_CONTEMPLATION_PROMPT, SYSTEM_CONTEMPLATION_AND_RECOMMENDATION_PROMPT

# Load variables from the .env file
load_dotenv()

## MODEL SETTINGS ##
api_key_load_balancer = LoadBalancer()
api_key_lock = threading.Lock()
def safe_getAvailableAPIKey(model_name):
    with api_key_lock:
        return api_key_load_balancer.getAvailableAPIKey(model_name)

generation_config = {
    "temperature": 1.0,
    "top_p": 0.95,
    "top_k": 40,
    "seed": 50,
    "max_output_tokens": 8192,
    "response_modalities": ["TEXT"],
}
## MODEL SETTINGS ##


def calculate_cost(input_tokens, output_tokens, model):
    # Define pricing
    pricing_pro = {
        "input": {"under_128k": 1.25 / 1_000_000, "over_128k": 2.50 / 1_000_000},
        "output": {"under_128k": 5.00 / 1_000_000, "over_128k": 10.00 / 1_000_000}
    }

    pricing_flash = {
        "input": {"under_128k": 0.075 / 1_000_000, "over_128k": 0.3 / 1_000_000},
        "output": {"under_128k": 0.15 / 1_000_000, "over_128k": 0.6 / 1_000_000}
    }

    pricing = pricing_flash if "flash" in model else pricing_pro

    def get_cost(input_tokens, output_tokens):
        if input_tokens <= 128_000:
            input_cost = input_tokens * pricing["input"]["under_128k"]
            output_cost = output_tokens * pricing["output"]["under_128k"]
        else:
            input_cost = input_tokens * pricing["input"]["over_128k"]
            output_cost = output_tokens * pricing["output"]["over_128k"]
        return input_cost + output_cost

    return get_cost(input_tokens, output_tokens)

def get_services_offered_data(services_json_path = "./services.json", list_output = False):
    with open(services_json_path, 'r') as file:
        services_data = json.load(file)

    def structure_service(service_datapoint):
        service_structurised = ""
        service_structurised += f"Section: {service_datapoint['section']}\n"
        service_structurised += f"Name: {service_datapoint['name']}\n"
        service_structurised += f"Summary: {service_datapoint['summary']}\n"
        service_structurised += f"Sample Situations / High Level Prerequisites: {service_datapoint['situations']}\n"
        return service_structurised

    SERVICES_DESCRIPTION = "<services_offered>\n"
    if list_output:
        SERVICES_DESCRIPTION = []

    for idx in range(1, len(services_data) + 1):
        if list_output:
            SERVICES_DESCRIPTION.append("<service_offered>\n" + structure_service(services_data[idx-1]) + "</service_offered>")
        else:
            SERVICES_DESCRIPTION += f"<service_idx_{idx}>\n"
            SERVICES_DESCRIPTION += f"IDX: {idx}\n"
            SERVICES_DESCRIPTION += structure_service(services_data[idx-1])
            SERVICES_DESCRIPTION += f"</service_idx_{idx}>\n"
            SERVICES_DESCRIPTION += f"\n"

    if list_output == False:
        SERVICES_DESCRIPTION += "</services_offered>"

    return SERVICES_DESCRIPTION

def extract_content(text, tag_name):
    # Build a regex pattern dynamically based on the tag_name
    pattern = rf"<{tag_name}>(.*?)</{tag_name}>"
    
    # Use re.findall to extract all matches
    matches = re.findall(pattern, text, re.DOTALL)
    
    return matches[0] if matches else None

def extract_and_process_tags(response_text):
    impact_score = extract_content(response_text, "impact_score")
    impact_score = impact_score if impact_score else -1
    try:
        impact_score = int(impact_score)
    except:
        impact_score = -1

    detailed_reasoning_report = extract_content(response_text, "detailed_reasoning_report")
    detailed_reasoning_report = detailed_reasoning_report if detailed_reasoning_report else ""

    return {
        "impact_score": impact_score,
        "detailed_reasoning_report": detailed_reasoning_report
    }

def process_contemplation_impact(trigger_document_path, company_book_path, model_name = "gemini-1.5-pro"):
    total_cost = 0
    start_time = time.time()

    generation_config["temperature"] = 0.7 if "flash-thinking" in model_name else 1.0
    generation_config["top_k"] = 40 if "flash-thinking" in model_name or "1.5-pro" in model_name else 64

    # try get api key from load balancer
    try:
        api_key = safe_getAvailableAPIKey(model_name)
        if api_key is None:
            return {
                "error": "All APIs are busy. Try again in some time.",
                "status_code": 420,
            }
    except:
        return {
            "error": str(e),
            "status_code": 401,
        }

    # try init client
    try:
        client = genai.Client(api_key = api_key)
    except:
        return {
            "error": str(e),
            "status_code": 402,
        }

    # try file upload
    try:
        trigger_data_source_pdf = client.files.upload(path = trigger_document_path)
        trigger_data_source_pdf_obj = types.Part.from_uri(
            file_uri = trigger_data_source_pdf.uri,
            mime_type = "application/pdf",
        )

        company_book_data_source_pdf = client.files.upload(path = company_book_path)
        company_book_data_source_pdf_obj = types.Part.from_uri(
            file_uri = company_book_data_source_pdf.uri,
            mime_type = "application/pdf",
        )
    except Exception as e:
        return {
            "error": str(e),
            "status_code": 403,
        }

    # try init chat instance with gemini
    try:
        # generation_config["top_k"] = 40 if "flash" in model_name else 64
        chat = client.chats.create(
                model = model_name,
                config = types.GenerateContentConfig(
                    temperature = generation_config["temperature"],
                    top_p = generation_config["top_p"],
                    top_k = generation_config["top_k"],
                    seed = generation_config["seed"],
                    max_output_tokens = generation_config["max_output_tokens"],
                    response_modalities = generation_config["response_modalities"],
                )
            )
    except Exception as e:
        return {
            "error": str(e),
            "status_code": 404,
        }
    
    # try getting gemini response
    responses = []
    try:
        impact_analysis_response = chat.send_message([SYSTEM_CONTEMPLATION_PROMPT, "<trigger_document>", trigger_data_source_pdf_obj, "</trigger_document>", "<company_book>", company_book_data_source_pdf_obj, "</company_book>"])
        total_cost += calculate_cost(impact_analysis_response.usage_metadata.prompt_token_count, impact_analysis_response.usage_metadata.candidates_token_count, model_name)
        impact_analysis_response = impact_analysis_response.text
        responses.append(impact_analysis_response)

        # continue loop, if <CHARLIEWAFFLES> not spotted
        continue_loop_limit = 2
        while "<CHARLIEWAFFLES>" not in impact_analysis_response and continue_loop_limit > 0:
            print("Continuing generation....")
            impact_analysis_response = chat.send_message(["Continue exactly from where you left off."])
            total_cost += calculate_cost(impact_analysis_response.usage_metadata.prompt_token_count, impact_analysis_response.usage_metadata.candidates_token_count, model_name)
            impact_analysis_response = impact_analysis_response.text
            responses.append(impact_analysis_response)
            continue_loop_limit -= 1

        impact_analysis_response = " ".join(responses)
        del responses
    except Exception as e:
        # print(str(e), " <<==>> ", api_key)
        return {
            "error": str(e),
            "status_code": 405,
        }
    
    # try remove the file from gemini cloud
    try:
        client.files.delete(name = trigger_data_source_pdf.name)
        client.files.delete(name = company_book_data_source_pdf.name)
    except Exception as e:
        return {
            "error": str(e),
            "status_code": 406,
        }
    
    end_time = time.time()
    time_taken = end_time - start_time

    return {
            "response": impact_analysis_response,
            "time_taken": time_taken,
            "total_cost": total_cost,
            "status_code": 200
        }

def process_contemplation_recommendation(trigger_document_path, company_book_path, impact_analysis_response, service_data, model_name = "gemini-1.5-pro"):
    total_cost = 0
    start_time = time.time()

    generation_config["temperature"] = 0.7 if "flash-thinking" in model_name else 1.0
    generation_config["top_k"] = 40 if "flash-thinking" in model_name or "1.5-pro" in model_name else 64

    # try get api key from load balancer
    try:
        api_key = safe_getAvailableAPIKey(model_name)
        if api_key is None:
            return {
                "error": "All APIs are busy. Try again in some time.",
                "status_code": 420,
            }
    except:
        return {
            "error": str(e),
            "status_code": 401,
        }

    # try init client
    try:
        client = genai.Client(api_key = api_key)
    except:
        return {
            "error": str(e),
            "status_code": 402,
        }

    # try file upload
    try:
        trigger_data_source_pdf = client.files.upload(path = trigger_document_path)
        trigger_data_source_pdf_obj = types.Part.from_uri(
            file_uri = trigger_data_source_pdf.uri,
            mime_type = "application/pdf",
        )

        company_book_data_source_pdf = client.files.upload(path = company_book_path)
        company_book_data_source_pdf_obj = types.Part.from_uri(
            file_uri = company_book_data_source_pdf.uri,
            mime_type = "application/pdf",
        )
    except Exception as e:
        return {
            "error": str(e),
            "status_code": 403,
        }

    # try init chat instance with gemini
    try:
        # generation_config["top_k"] = 40 if "flash" in model_name else 64

        chat_history = [
                types.Content(
                    role = 'user',
                    parts = [
                        types.Part.from_text(SYSTEM_CONTEMPLATION_PROMPT),
                        types.Part.from_text("<trigger_document>"),
                        trigger_data_source_pdf_obj,
                        types.Part.from_text("</trigger_document> \n\n <company_book>"),
                        company_book_data_source_pdf_obj,
                        types.Part.from_text("</company_book>"),
                    ]
                ),
                types.Content(
                    role = 'assistant',
                    parts = [
                        types.Part.from_text(impact_analysis_response),
                    ]
                ),
        ]
        
        chat = client.chats.create(
                model = model_name,
                config = types.GenerateContentConfig(
                    temperature = generation_config["temperature"],
                    top_p = generation_config["top_p"],
                    top_k = generation_config["top_k"],
                    seed = generation_config["seed"],
                    max_output_tokens = generation_config["max_output_tokens"],
                    response_modalities = generation_config["response_modalities"],
                ),
                history = chat_history,
            )
    except Exception as e:
        return {
            "error": str(e),
            "status_code": 404,
        }

    # try getting gemini response
    responses = []
    try:
        recommendation_response = chat.send_message([SYSTEM_CONTEMPLATION_AND_RECOMMENDATION_PROMPT, service_data])
        total_cost += calculate_cost(recommendation_response.usage_metadata.prompt_token_count, recommendation_response.usage_metadata.candidates_token_count, model_name)
        recommendation_response = recommendation_response.text
        responses.append(recommendation_response)

        # continue loop, if <CHARLIEWAFFLES> not spotted
        continue_loop_limit = 2
        while "<CHARLIEWAFFLES>" not in recommendation_response and continue_loop_limit > 0:
            print("Continuing generation....")
            recommendation_response = chat.send_message(["Continue exactly from where you left off."])
            total_cost += calculate_cost(recommendation_response.usage_metadata.prompt_token_count, recommendation_response.usage_metadata.candidates_token_count, model_name)
            recommendation_response = recommendation_response.text
            responses.append(recommendation_response)
            continue_loop_limit -= 1

        recommendation_response = " ".join(responses)
        del responses
    except Exception as e:
        # print(str(e), " <<==>> ", api_key)
        return {
            "error": str(e),
            "status_code": 405,
        }
    
    # try remove the file from gemini cloud
    try:
        client.files.delete(name = trigger_data_source_pdf.name)
        client.files.delete(name = company_book_data_source_pdf.name)
    except Exception as e:
        return {
            "error": str(e),
            "status_code": 406,
        }
    
    end_time = time.time()
    time_taken = end_time - start_time

    return {
            "response": recommendation_response,
            "time_taken": time_taken,
            "total_cost": total_cost,
            "status_code": 200
        }

def process_impact_and_recommendation(trigger_document_path, company_book_path, model_name = "gemini-1.5-pro"):
    # Suppose this returns a list of items you want to process
    all_services_data = get_services_offered_data(list_output = False)

    total_cost = 0
    start_time = time.time()

    generation_config["temperature"] = 0.7 if "flash-thinking" in model_name else 1.0
    generation_config["top_k"] = 40 if "flash-thinking" in model_name or "1.5-pro" in model_name else 64

    # try get api key from load balancer
    try:
        api_key = safe_getAvailableAPIKey(model_name)
        if api_key is None:
            return {
                "error": "All APIs are busy. Try again in some time.",
                "status_code": 420,
            }
    except:
        return {
            "error": str(e),
            "status_code": 401,
        }

    # try init client
    try:
        client = genai.Client(api_key = api_key)
    except:
        return {
            "error": str(e),
            "status_code": 402,
        }

    # try file upload
    try:
        trigger_data_source_pdf = client.files.upload(path = trigger_document_path)
        trigger_data_source_pdf_obj = types.Part.from_uri(
            file_uri = trigger_data_source_pdf.uri,
            mime_type = "application/pdf",
        )

        company_book_data_source_pdf = client.files.upload(path = company_book_path)
        company_book_data_source_pdf_obj = types.Part.from_uri(
            file_uri = company_book_data_source_pdf.uri,
            mime_type = "application/pdf",
        )
    except Exception as e:
        return {
            "error": str(e),
            "status_code": 403,
        }

    # try init chat instance with gemini
    try:
        # generation_config["top_k"] = 40 if "flash" in model_name else 64
        chat = client.chats.create(
                model = model_name,
                config = types.GenerateContentConfig(
                    temperature = generation_config["temperature"],
                    top_p = generation_config["top_p"],
                    top_k = generation_config["top_k"],
                    seed = generation_config["seed"],
                    max_output_tokens = generation_config["max_output_tokens"],
                    response_modalities = generation_config["response_modalities"],
                )
            )
    except Exception as e:
        return {
            "error": str(e),
            "status_code": 404,
        }
    
    # try getting gemini response
    responses = []
    try:
        impact_analysis_response = chat.send_message([SYSTEM_PROMPT, "<trigger_document>", trigger_data_source_pdf_obj, "</trigger_document>", "<company_book>", company_book_data_source_pdf_obj, "</company_book>"])
        total_cost += calculate_cost(impact_analysis_response.usage_metadata.prompt_token_count, impact_analysis_response.usage_metadata.candidates_token_count, model_name)
        impact_analysis_response = impact_analysis_response.text
        responses.append(impact_analysis_response)

        # continue loop, if <CHARLIEWAFFLES> not spotted
        continue_loop_limit = 0
        while "<CHARLIEWAFFLES>" not in impact_analysis_response and continue_loop_limit > 0:
            print("Continuing generation....")
            impact_analysis_response = chat.send_message(["Continue exactly from where you left off."])
            total_cost += calculate_cost(impact_analysis_response.usage_metadata.prompt_token_count, impact_analysis_response.usage_metadata.candidates_token_count, model_name)
            impact_analysis_response = impact_analysis_response.text
            responses.append(impact_analysis_response)
            continue_loop_limit -= 1

        impact_analysis_response = " ".join(responses)
        del responses
    except Exception as e:
        # print(str(e), " <<==>> ", api_key)
        return {
            "error": str(e),
            "status_code": 405,
        }
    
    # try getting gemini response
    responses = []
    try:
        recommendation_response = chat.send_message([SERVICES_OFFER_PROMPT, all_services_data])
        total_cost += calculate_cost(recommendation_response.usage_metadata.prompt_token_count, recommendation_response.usage_metadata.candidates_token_count, model_name)
        recommendation_response = recommendation_response.text
        responses.append(recommendation_response)

        # continue loop, if <CHARLIEWAFFLES> not spotted
        continue_loop_limit = 0
        while "<CHARLIEWAFFLES>" not in recommendation_response and continue_loop_limit > 0:
            print("Continuing generation....")
            recommendation_response = chat.send_message(["Continue exactly from where you left off."])
            total_cost += calculate_cost(recommendation_response.usage_metadata.prompt_token_count, recommendation_response.usage_metadata.candidates_token_count, model_name)
            recommendation_response = recommendation_response.text
            responses.append(recommendation_response)
            continue_loop_limit -= 1

        recommendation_response = " ".join(responses)
        del responses
    except Exception as e:
        # print(str(e), " <<==>> ", api_key)
        return {
            "error": str(e),
            "status_code": 406,
        }
    
    # try remove the file from gemini cloud
    try:
        client.files.delete(name = trigger_data_source_pdf.name)
        client.files.delete(name = company_book_data_source_pdf.name)
    except Exception as e:
        return {
            "error": str(e),
            "status_code": 407,
        }
    
    end_time = time.time()
    time_taken = end_time - start_time

    return {
            "response": recommendation_response,
            "time_taken": time_taken,
            "total_cost": total_cost,
            "status_code": 200
        }

def process_contemplation_impact_and_recommendation_parallel(trigger_document_path, company_book_path, model_name = "gemini-1.5-pro"):
    # Suppose this returns a list of items you want to process
    all_services_data = get_services_offered_data(list_output = True)

    try:
        impact_analysis_response = process_contemplation_impact(
                                        trigger_document_path = trigger_document_path, 
                                        company_book_path = company_book_path,
                                        model_name = model_name
                                    )
        
        if "error" in impact_analysis_response:
            return impact_analysis_response
    except Exception as e:
        return {
                "error": str(e),
                "status_code": 401,
            }

    # Decide number_of_threads
    number_of_threads = len(all_services_data)
    # Decide max number of retries
    max_retries = 2

    def process_with_retry(idx, service_data):
        """
        Inner helper function that wraps process_COATT_single_thread with retry logic
        and attaches the original index to the response.
        """
        attempts = 0
        final_response = None

        while attempts < max_retries:
            response = process_contemplation_recommendation(trigger_document_path, company_book_path, impact_analysis_response["response"], service_data, model_name)
            
            # If no error in response, mark success and break
            if "error" not in response:
                response["original_idx"] = idx
                response["service_data"] = service_data  # attach service_data for later use
                return response

            # Otherwise, increment attempt counter and possibly retry
            attempts += 1
            final_response = response
            time.sleep(15)  # Wait before retrying

        # If we exhausted retries, attach original_idx anyway and return last error response
        final_response["original_idx"] = idx
        final_response["service_data"] = service_data
        return final_response

    start_time = time.time()
    results = []

    # Initialize tqdm for successful requests
    # total = number of data items, but we only increment when a request is successful
    progress_bar = tqdm(total=len(all_services_data), desc="Processing COATT")

    with concurrent.futures.ThreadPoolExecutor(max_workers=number_of_threads) as executor:
        future_to_index = {
            executor.submit(process_with_retry, i, service_data): i
            for i, service_data in enumerate(all_services_data)
        }

        for future in concurrent.futures.as_completed(future_to_index):
            idx = future_to_index[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                # In case something unexpected happens
                results.append({
                    "error": str(e),
                    "status_code": 501,
                    "original_idx": idx
                })
            progress_bar.update(1)

    # Close the progress bar so final state is displayed properly
    progress_bar.close()

    # Post-processing: filter results
    filtered_results = []
    for dp in results:
        try:
            # We only parse if "response" is present (i.e., a normal shape)
            if "response" in dp:
                dp_parsed = extract_and_process_tags(dp["response"])
                
                # Example of applying some filter
                if dp_parsed["impact_score"] > 7:
                    service_filter_infoed = dp["service_data"][:dp["service_data"].index("Summary: ")]\
                        .replace("<service_offered>\n", "")\
                        .replace("<\\service_offered>", "")
                    
                    filtered_results.append(
                        service_filter_infoed + "\n" +
                        f"IMPACT SCORE:- {dp_parsed['impact_score']}\n" +
                        "REASONING REPORT:-\n" + 
                        dp_parsed["detailed_reasoning_report"] + "\n\n"
                    )
        except Exception as e:
            print("Error in filtering block:", str(e))

    end_time = time.time()
    time_taken = end_time - start_time
    time_taken += impact_analysis_response["time_taken"]
    print(f"Time Taken: {int(time_taken)} seconds")

    # Summation of total cost for successful or partially successful requests
    total_cost = impact_analysis_response["total_cost"]
    total_cost += sum([dp["total_cost"] for dp in results if "total_cost" in dp])
    print(f"Total cost: ${round(total_cost, 2)}")

    failed_requests = sum([1 for dp in results if "error" in dp])
    print(f"Failed Requests: {failed_requests}")

    # optionally save the load balancer stats
    # api_key_load_balancer.save_overall_stats_to_file()

    return {
            "response": filtered_results,
            "time_taken": time_taken,
            "total_cost": total_cost
        }

if __name__ == "__main__":
    response = process_impact_and_recommendation(
                trigger_document_path = "/Users/adityasihag/Desktop/projects/KL_insight/gdpr.pdf", 
                company_book_path = "/Users/adityasihag/Desktop/projects/KL_insight/27-jaguar-land-rover-limited - UK.pdf",
                model_name = "gemini-2.0-flash-exp"
            )

    # response = process_contemplation_impact_and_recommendation_parallel(
    #             trigger_document_path = "/Users/adityasihag/Desktop/projects/KL_insight/gdpr.pdf", 
    #             company_book_path = "/Users/adityasihag/Desktop/projects/KL_insight/27-jaguar-land-rover-limited - UK.pdf",
    #             model_name = "gemini-2.0-flash-exp"
    #         )
    
    print(response)